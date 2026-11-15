import type { EpisodeEdit } from './episode-editor.js';
/** Standalone episode surface. E03 supplies session transport, words and the mounting element. */
export type Task = {
  task_id: string; revision: number; kind: string; action: string; state: string;
  due_at: string | null; due_status: string; overdue: boolean;
  owner: { id: string; display_name?: string | null }; allowed_actions?: string[];
};
export type Episode = {
  episode_id: string; revision: number; state: string; urgency_state: string; origin_mode: string;
  concern_text: string; allowed_actions: string[]; followup_tasks: Task[];
  withheld_answer_count: number; resolve_reasons?: string[]; review_ids?: string[]; observation_ids?: string[];
};
export type Words = {
  states: Record<string, string>; origins: Record<string, string>; taskStates: Record<string, string>;
  actions: Record<string, string>; errors: Record<string, string> & { unknown: string }; overdue: string; duePending: string;
  stale: string; offline: string; withheld: string; timeline: string; concern: string; lateEntry?: string;
  timelineKinds?: Record<string,string>; reviewDecisions?: Record<string,string>; reviewRoles?: Record<string,string>; groundAdvice?: string;
};
export type Timeline = { next_cursor?: string | null; revision: number; items: {kind: string; occurred_at: string; recorded_at?: string; late?: boolean; state: string | null; actor: {id: string} | null}[] };
export type Review = {review_id: string; role: string; reviewer: {id: string; display_name?: string | null}; signed_at: string; decisions: {item: string; decision: string; rationale: string}[]; unresolved: string[]};
export type Transport = {
  timeline?(path: string, signal: AbortSignal): Promise<Timeline>;
  reviews?(path: string, signal: AbortSignal): Promise<{revision: number; items: Review[]; next_cursor?: string | null}>;
  review?(path: string, body: Record<string, unknown>, revision: number, key: string, signal: AbortSignal): Promise<Review>;
  get(path: string, signal: AbortSignal): Promise<Episode>;
  command(path: string, body: Record<string, unknown>, revision: number, key: string, signal: AbortSignal): Promise<Episode>;
};
export function visibleActions(episode: Episode, stale: boolean): readonly string[] {
  return stale ? [] : episode.allowed_actions.filter(action => action !== 'share');
}
export function taskStatus(task: Task, words: Words): string {
  return [words.taskStates[task.state] ?? words.errors.unknown,
    task.overdue ? words.overdue : '', task.due_at ?? (task.due_status === 'threshold_pending' ? words.duePending : '')]
    .filter(Boolean).join(' ');
}
export class EpisodePanel {
  private editorController: AbortController | null = null;
  private editing = false;
  private generation = 0;
  private controller: AbortController | null = null;
  private value: Episode | null = null;
  private stale = true;
  private timeline: Timeline | null = null;
  private reviews: Review[] = [];
  private reviewCursor: string | null = null;
  private paging = false;
  private pending = false;
  private loading = false;
  private episodeId: string | null = null;
  private retry: { body: Record<string, unknown>; key: string; revision: number; operation: 'action' | 'review' } | null = null;
  private root: HTMLElement;
  private transport: Transport;
  private words: Words;
  private key: () => string;
  private edit: EpisodeEdit;
  constructor(root: HTMLElement, transport: Transport, words: Words, key: () => string,
    edit: EpisodeEdit) {
    if (!words.errors.unknown?.trim()) throw new Error('episode words unavailable');
    this.root = root; this.transport = transport; this.words = words; this.key = key; this.edit = edit;
  }
  clear(): void {
    this.editorController?.abort(); this.editorController = null; this.editing = false;
    this.generation++; this.controller?.abort(); this.controller = null;
    this.value = null; this.timeline = null; this.reviews = []; this.reviewCursor = null; this.paging = false; this.episodeId = null; this.retry = null; this.pending = false;
    this.loading = false; this.root.replaceChildren();
  }
  async open(episodeId: string): Promise<void> {
    this.clear(); this.episodeId = episodeId; await this.refresh();
  }
  async refresh(): Promise<void> {
    if (this.loading) { this.pending = true; return; }
    if (!this.episodeId) return;
    this.loading = true;
    let reviewMismatch = false;
    const generation = this.generation;
    const controller = new AbortController(); this.controller = controller;
    try {
      const value = await this.transport.get(`/v1/episodes/${encodeURIComponent(this.episodeId)}`, controller.signal);
      if (generation !== this.generation) return;
      this.value = value;
      if (this.transport.timeline) this.timeline = await this.transport.timeline(`/v1/episodes/${encodeURIComponent(this.episodeId)}/timeline`, controller.signal);
      if (this.transport.reviews) { const reviews = await this.transport.reviews(`/v1/episodes/${encodeURIComponent(this.episodeId)}/reviews`, controller.signal); this.reviews = reviews.items; this.reviewCursor = reviews.next_cursor ?? null; reviewMismatch = reviews.revision !== value.revision; }
      if (generation !== this.generation) return;
      this.stale = reviewMismatch || (this.timeline !== null && this.timeline.revision !== value.revision); this.render();
    } catch {
      if (generation !== this.generation || controller.signal.aborted) return;
      this.stale = true; this.render();
    } finally {
      if (generation === this.generation) {
        this.loading = false;
        if (this.pending) { this.pending = false; await this.refresh(); }
      }
    }
  }
  streamLost(): void { this.editorController?.abort(); this.stale = true; this.render(); }
  async streamRestored(): Promise<void> { await this.refresh(); }
  async act(action: string,task?: Task): Promise<void> {
    const episode = this.value;
    if (!episode || this.editing || this.stale || !(task ? task.allowed_actions ?? [] : episode.allowed_actions).includes(action)) return;
    const generation = this.generation;
    const editor = new AbortController(); this.editorController = editor; this.editing = true;
    let body: Record<string,unknown> | null;
    try { body = await this.edit(action, episode,task,editor.signal); }
    finally { if (generation === this.generation) this.editing = false; }
    if (!body || this.stale || editor.signal.aborted || generation !== this.generation) return;
    if (action === 'record_review' && !this.transport.review) return;
    this.retry = { body: action === 'record_review' ? body : { ...body, action, ...(task ? {task_id:task.task_id,task_revision:task.revision}: {}) },
      key: this.key(), revision: episode.revision, operation: action === 'record_review' ? 'review' : 'action' };
    await this.retryCommand();
  }
  async retryCommand(): Promise<void> {
    const retry = this.retry; const episode = this.value;
    if (!retry || !episode) return;
    const generation = this.generation; const controller = new AbortController();
    this.controller?.abort(); this.controller = controller;
    try {
      if (retry.operation === 'review') {
        if (!this.transport.review) return;
        await this.transport.review(`/v1/episodes/${encodeURIComponent(episode.episode_id)}/reviews`,retry.body,retry.revision,retry.key,controller.signal);
      } else {
        await this.transport.command(`/v1/episodes/${encodeURIComponent(episode.episode_id)}/actions`,retry.body,retry.revision,retry.key,controller.signal);
      }
      if (generation !== this.generation) return;
      this.retry = null; this.stale = true; await this.refresh();
    } catch {
      if (generation !== this.generation || controller.signal.aborted) return;
      this.stale = true; this.render();
    }
  }
  async more(kind: 'timeline' | 'reviews'): Promise<void> {
    const cursor = kind === 'timeline' ? this.timeline?.next_cursor : this.reviewCursor;
    if (this.paging || this.stale || !this.episodeId || !cursor) return;
    this.paging = true; const generation=this.generation; const controller=new AbortController(); this.controller?.abort(); this.controller=controller;
    try {
      const path=`/v1/episodes/${encodeURIComponent(this.episodeId)}/${kind}?cursor=${encodeURIComponent(cursor)}`;
      if (kind === 'timeline' && this.transport.timeline && this.timeline) {
        const page = await this.transport.timeline(path,controller.signal);
        if (generation !== this.generation || controller.signal.aborted) return;
        if (page.revision !== this.value?.revision || page.next_cursor === cursor) { this.stale=true; return; }
        this.timeline={...page,items:[...this.timeline.items,...page.items]};
      } else if (kind === 'reviews' && this.transport.reviews) {
        const page=await this.transport.reviews(path,controller.signal);
        if (generation !== this.generation || controller.signal.aborted) return;
        if (page.revision !== this.value?.revision || page.next_cursor === cursor) { this.stale=true; return; }
        this.reviews=[...this.reviews,...page.items];this.reviewCursor=page.next_cursor ?? null;
      }
    } catch { if(generation === this.generation) this.stale=true; }
    finally { if(generation === this.generation) {this.paging=false;this.render();} }
  }
  private node(tag: string, content: string): HTMLElement {
    const element = document.createElement(tag); element.textContent = content; return element;
  }
  private render(): void {
    const episode = this.value;
    this.root.replaceChildren();
    if (!episode) { this.root.append(this.node('p', this.words.offline)); return; }
    const state = this.node('p', this.words.states[episode.state] ?? this.words.errors.unknown);
    state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
    this.root.append(this.node('h2', this.words.concern), state,
      this.node('p', this.words.origins[episode.origin_mode] ?? this.words.errors.unknown), this.node('p', episode.concern_text));
    if (this.stale) this.root.append(this.node('p', this.words.stale));
    if (this.stale && this.retry && this.words.actions.retry) {
      const button=this.node('button',this.words.actions.retry) as HTMLButtonElement; button.type='button';
      button.addEventListener('click',()=>{void this.retryCommand();});this.root.append(button);
    }
    if (episode.withheld_answer_count) this.root.append(this.node('p', `${this.words.withheld} ${episode.withheld_answer_count}`));
    const list = document.createElement('ul');
    for (const task of episode.followup_tasks) {
      const row = document.createElement('li');
      row.append(this.node('p', task.action), this.node('p', task.owner.display_name ?? task.owner.id), this.node('p', taskStatus(task, this.words)));
      if (!this.stale) for (const action of task.allowed_actions ?? []) {
        const label = this.words.actions[action]; if (!label) continue;
        const button = this.node('button',label) as HTMLButtonElement; button.type='button';
        button.addEventListener('click',()=>{void this.act(action,task);}); row.append(button);
      }
      list.append(row);
    }
    this.root.append(list);
    if (this.timeline) {
      const history = document.createElement('ol'); history.setAttribute('aria-label', this.words.timeline);
      for (const item of this.timeline.items) history.append(this.node('li', [this.words.timelineKinds?.[item.kind] ?? this.words.errors.unknown, item.occurred_at, item.actor?.id ?? '', item.state ?? '', item.late ? this.words.lateEntry ?? this.words.errors.unknown : ''].filter(Boolean).join(' ')));
      this.root.append(history);
    }
    for (const review of this.reviews) {
      const section = document.createElement('section');
      section.append(this.node('p', this.words.reviewRoles?.[review.role] ?? this.words.errors.unknown));
      if (review.role === 'ground') section.append(this.node('p',this.words.groundAdvice ?? this.words.errors.unknown));
      section.append(this.node('p', review.reviewer.display_name ?? review.reviewer.id), this.node('p', review.signed_at));
      for (const decision of review.decisions) section.append(this.node('p', decision.item), this.node('p',this.words.reviewDecisions?.[decision.decision] ?? this.words.errors.unknown), this.node('p', decision.rationale));
      for (const unresolved of review.unresolved) section.append(this.node('p', unresolved));
      this.root.append(section);
    }
    if (!this.stale) for (const kind of ['timeline','reviews'] as const) {
      const cursor=kind==='timeline'?this.timeline?.next_cursor:this.reviewCursor;
      const label=this.words.actions['more_'+kind];
      if(cursor && label) {const button=this.node('button',label) as HTMLButtonElement;button.type='button';button.disabled=this.paging;button.addEventListener('click',()=>{void this.more(kind);});this.root.append(button);}
    }
    for (const action of visibleActions(episode, this.stale)) {
      const label = this.words.actions[action];
      if (!label) continue;
      const button = this.node('button', label) as HTMLButtonElement;
      button.type = 'button'; button.addEventListener('click', () => { void this.act(action); });
      this.root.append(button);
    }
  }
}

/** Same-origin session transport. E03 supplies authenticated headers, never an actor or patient body override. */
export function httpEpisodeTransport(headers: () => Record<string, string>): Transport {
  async function read<T>(path: string, options: RequestInit): Promise<T> {
    const response = await fetch(path, { ...options, credentials: 'same-origin', headers: {
      ...headers(), Accept: 'application/vnd.crew-health.episode+json', ...options.headers,
    } });
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    return await response.json() as T;
  }
  return {
    get: (path, signal) => read<Episode>(path, { signal }),
    timeline: (path, signal) => read<Timeline>(path, { signal }),
    reviews: (path, signal) => read<{revision: number; items: Review[]; next_cursor?: string | null}>(path, { signal }),
    review: (path, body, revision, key, signal) => read<Review>(path, { method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'If-Match': `"${revision}"`, 'Idempotency-Key': key }, body: JSON.stringify(body) }),
    command: (path, body, revision, key, signal) => read<Episode>(path, { method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', 'If-Match': `"${revision}"`, 'Idempotency-Key': key },
      body: JSON.stringify(body),
    }),
  };
}
