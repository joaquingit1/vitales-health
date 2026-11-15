from crew_health.episodes.application.codec import instant, text
from crew_health.episodes.application.use_cases import EpisodeService, parse_time
from crew_health.kernel.uow import UnitOfWork


class NoticeOverdue:
    def __init__(self, service: EpisodeService) -> None:
        self.service = service

    def run(self, uow: UnitOfWork) -> int:
        s = self.service
        rows = uow.connection.rows(
            "SELECT t.task_id,t.episode_id,t.due_at FROM episodes_task t LEFT JOIN episodes_task_overdue n ON n.task_id=t.task_id WHERE t.state=? AND t.due_at<? AND n.task_id IS NULL ORDER BY t.due_at,t.task_id LIMIT ?",
            ("open", instant(uow.now), s.limits.overdue_batch_size),
            limit=s.limits.overdue_batch_size,
        )
        for row in rows:
            ep = s.repo.require(uow, text(row[1]))
            uow.connection.execute(
                "INSERT INTO episodes_task_overdue VALUES (?,?,?)", (row[0], row[2], instant(uow.now))
            )
            s.changed(uow, ep, "followup_overdue", cause=text(row[0]), occurred_at=parse_time(row[2]))
        return len(rows)
