from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from crew_health_bench.adapters.case_documents import CaseDocuments, LoadedCase
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.rubric_grader import evaluate_rubric
from crew_health_bench.adapters.vault import AgeVault
from crew_health_bench.contracts_gen.trace import CaseTrace as TraceDocument
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation, Grade
from crew_health_bench.domain.trace import CaseTrace


class SuiteGrader:
    def __init__(self, *, root: Path, cases: Mapping[str, LoadedCase], documents: CaseDocuments,
                 max_file_bytes: int, hidden_dir: Path | None = None, vault: AgeVault | None = None) -> None:
        if (hidden_dir is None) == (vault is None):
            raise BenchError("vault_locked", exit_code=5)
        self.root, self.cases, self.documents = root, cases, documents
        self.maximum, self.hidden_dir, self.vault = max_file_bytes, hidden_dir, vault
        self.trace = TypeAdapter(CaseTrace)

    def grade(self, generation: Generation) -> Grade:
        if generation.case_id not in self.cases:
            raise BenchError("case_invalid")
        if generation.status != "completed":
            return Grade(generation.case_id, generation.repeat, (), None, 0, 0)
        case = self.cases[generation.case_id]
        if self.vault is not None:
            receipt = self.vault.verify(case.case.case_id)
            if receipt["hidden_sha256"] != case.case.hidden_sha256:
                raise BenchError("integrity", exit_code=6)
            hidden = self.documents.parse_hidden(self.vault.open_hidden(case.case.case_id))
            if hidden.canary != case.case.canary:
                raise BenchError("case_invalid")
        elif self.hidden_dir is not None:
            hidden, _ = self.documents.hidden_for(case, self.hidden_dir / (case.case.case_id + ".json"))
        else:
            raise BenchError("vault_locked", exit_code=5)
        path = vault_path(self.root, generation.trace_ref)
        try:
            with path.open("rb") as stream:
                raw = stream.read(self.maximum + 1)
            if len(raw) > self.maximum or sha256(raw).hexdigest() != generation.output_sha256:
                raise BenchError("integrity", exit_code=6)
            document = TraceDocument.model_validate_json(raw, strict=True)
            trace = self.trace.validate_json(document.model_dump_json(), strict=True)
        except ValidationError as error:
            raise BenchError("case_invalid") from error
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        return Grade(generation.case_id, generation.repeat, evaluate_rubric(hidden, trace), None, 0, 0)
