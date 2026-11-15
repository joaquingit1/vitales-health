import type { Episode, Task } from './episode-panel.js';

export type EditorWords = {
  actions: Record<string,string>; fields: Record<string,string>; submit: string; cancel: string;
  addDecision: string; decisions: Record<string,string>; resolutionReasons: Record<string,string>;
  reopenReasons: Record<string,string>; onsetPrecisions: Record<string,string>;
};
export type Choice = { id: string; label: string };
export type EditorChoices = { owners: readonly Choice[]; observations: readonly Choice[]; reviews: readonly Choice[]; maxReviewDecisions: number };
export type EpisodeEdit = (action: string, episode: Episode, task?: Task, signal?: AbortSignal) => Promise<Record<string,unknown> | null>;

/** Native dialog and form controls retain browser keyboard, focus and required-field behavior.
 * E03 supplies approved words and currently authorized choices. No chart is fetched by this editor.
 */
export function episodeEditor(host: HTMLElement, words: EditorWords, choices: () => EditorChoices): EpisodeEdit {
  let closeCurrent: (() => void) | null = null;
  return async (action, _episode, _task, signal) => {
    closeCurrent?.();
    if (signal?.aborted) return null;
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      const form = document.createElement('form');
      const titleText = words.actions[action];
      if (!titleText) { resolve(null); return; }
      const title = document.createElement('h2'); title.textContent = titleText;
      dialog.append(title, form); host.append(dialog);
      const values = new Map<string, HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>();
      const labelFor = (key: string, control: HTMLElement, parent: HTMLElement = form): void => {
        const text = words.fields[key];
        if (!text) throw new Error('episode editor field word unavailable: ' + key);
        const label = document.createElement('label'); label.append(document.createTextNode(text), control); parent.append(label);
      };
      const input = (key: string, required = false, multiline = false, parent: HTMLElement = form): HTMLInputElement | HTMLTextAreaElement => {
        const control = multiline ? document.createElement('textarea') : document.createElement('input');
        control.name = key; control.required = required; control.maxLength = 500;
        labelFor(key, control, parent); values.set(key, control); return control;
      };
      const select = (key: string, options: readonly Choice[], required = true, multiple = false, parent: HTMLElement = form): HTMLSelectElement => {
        const control = document.createElement('select'); control.name = key; control.required = required; control.multiple = multiple;
        if (!multiple) control.append(new Option('', ''));
        for (const option of options) control.append(new Option(option.label, option.id));
        labelFor(key, control, parent); values.set(key, control); return control;
      };
      const enumChoices = (mapping: Record<string,string>): Choice[] => Object.entries(mapping).map(([id,label]) => ({id,label}));
      const current = choices();
      const rows: {item: HTMLInputElement | HTMLTextAreaElement; decision: HTMLSelectElement; rationale: HTMLInputElement | HTMLTextAreaElement}[] = [];
      const text = (key: string): string => values.get(key)?.value ?? '';
      const optional = (body: Record<string,unknown>, key: string): void => { if (text(key).trim()) body[key] = text(key).trim(); };
      const selected = (key: string): string[] => Array.from((values.get(key) as HTMLSelectElement).selectedOptions, option => option.value);
      const finish = (body: Record<string,unknown> | null): void => {
        signal?.removeEventListener('abort', abort); dialog.close(); dialog.remove();
        if (closeCurrent === abort) closeCurrent = null; resolve(body);
      };
      const abort = (): void => finish(null); closeCurrent = abort;
      dialog.addEventListener('cancel', event => { event.preventDefault(); finish(null); });
      signal?.addEventListener('abort', abort, {once:true});
      try {
        if (action === 'add_followup') {
          input('action_text', true, true); select('owner_person_id', current.owners, false);
          const due = input('due_at'); (due as HTMLInputElement).type = 'datetime-local';
        } else if (action === 'complete_followup') { input('note', false, true); }
        else if (action === 'cancel_followup') { input('cancellation_reason', true, true); }
        else if (action === 'reassign_followup') { select('owner_person_id', current.owners); }
        else if (action === 'request_review') { input('note', false, true); }
        else if (action === 'resolve') {
          select('reason', enumChoices(words.resolutionReasons).filter(choice=>(_episode.resolve_reasons ?? []).includes(choice.id))); input('rationale', false, true);
          select('observation_ids', current.observations.length ? current.observations : (_episode.observation_ids ?? []).map(id=>({id,label:id})), false, true); select('review_id', current.reviews.length ? current.reviews : (_episode.review_ids ?? []).map(id=>({id,label:id})), false);
        } else if (action === 'reopen') { select('reason', enumChoices(words.reopenReasons)); input('note', false, true); }
        else if (action === 'correct_onset') {
          select('precision', enumChoices(words.onsetPrecisions));
          const at = input('at'); (at as HTMLInputElement).type = 'datetime-local';
        } else if (action === 'record_review') {
          const addRow = (): void => {
            const row = document.createElement('fieldset'); form.append(row);
            rows.push({item: input('item', true, false, row), decision: select('decision', enumChoices(words.decisions), true, false, row), rationale: input('rationale', true, true, row)});
          };
          if (!Number.isInteger(current.maxReviewDecisions) || current.maxReviewDecisions < 1) throw new Error("review bound unavailable");
          addRow();
          const add = document.createElement('button'); add.type = 'button'; add.textContent = words.addDecision;
          add.addEventListener('click', () => { if (rows.length < current.maxReviewDecisions) addRow(); add.disabled = rows.length >= current.maxReviewDecisions; }); form.append(add); input('unresolved', false, true);
        } else if (action !== 'acknowledge') { finish(null); return; }
      } catch (error) { finish(null); return; }
      const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = words.submit;
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = words.cancel;
      cancel.addEventListener('click', () => finish(null)); form.append(submit, cancel);
      form.addEventListener('submit', event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const body: Record<string,unknown> = {};
        if (action === 'record_review') {
          body.decisions = rows.map(row => ({item:row.item.value.trim(), decision:row.decision.value, rationale:row.rationale.value.trim()}));
          body.unresolved = text('unresolved').split('\n').map(value=>value.trim()).filter(Boolean);
        } else if (action === 'correct_onset') {
          body.onset = {precision:text('precision'), at:text('at') ? new Date(text('at')).toISOString() : null};
        } else {
          for (const key of ['action_text','owner_person_id','note','cancellation_reason','reason','rationale','review_id']) optional(body,key);
          if (action === 'resolve') body.observation_ids = selected('observation_ids');
          if (text('due_at')) body.due_at = new Date(text('due_at')).toISOString();
        }
        finish(body);
      });
      dialog.showModal();
    });
  };
}
