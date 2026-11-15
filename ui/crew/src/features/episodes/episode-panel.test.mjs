import assert from 'node:assert/strict';
import { test } from 'node:test';
import { visibleActions, taskStatus } from './episode-panel.ts';
const episode = { allowed_actions: ['acknowledge', 'resolve', 'share'] };
test('offline surface never offers a command', () => {
  assert.deepEqual(visibleActions(episode, true), []);
  assert.deepEqual(visibleActions(episode, false), ['acknowledge', 'resolve']);
});
test('task words come from supplied configuration and retain overdue status', () => {
  const task = { state: 'open', due_at: null, due_status: 'threshold_pending', overdue: true };
  const words = { taskStates: { open: 'configured-open' }, errors: { unknown: 'unknown' }, overdue: 'configured-late', duePending: 'configured-pending' };
  assert.equal(taskStatus(task, words), 'configured-open configured-late configured-pending');
  assert.equal(taskStatus({ ...task, due_at: '2026-11-15T12:00:00Z' }, words), 'configured-open configured-late 2026-11-15T12:00:00Z');
});

test('transport carries the opt-in contract and preserves a command identity across retry', async () => {
  const { httpEpisodeTransport } = await import('./episode-panel.ts');
  const oldFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return { ok: true, json: async () => episode };
  };
  try {
    const transport = httpEpisodeTransport(() => ({ Authorization: 'explicit-local-test' }));
    const signal = new AbortController().signal;
    await transport.get('/v1/episodes/epi_one', signal);
    for (let i = 0; i < 2; i++) await transport.command('/v1/episodes/epi_one/actions', { action: 'acknowledge' }, 7, 'cmd_one', signal);
    assert.equal(calls[0].options.headers.Accept, 'application/vnd.crew-health.episode+json');
    assert.equal(calls[1].options.headers['Idempotency-Key'], calls[2].options.headers['Idempotency-Key']);
    assert.equal(calls[1].options.headers['If-Match'], '"7"');
    assert.equal(calls[1].options.credentials, 'same-origin');
  } finally { globalThis.fetch = oldFetch; }
});

class NodeStub {
  children = [];
  textContent = '';
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
}
const fullWords = { states: { open: 'Open' }, origins: { SIM: 'SIM' }, taskStates: {}, actions: { acknowledge: 'Acknowledge', record_review: 'Review', retry: 'Retry' }, errors: { unknown: 'Unknown' }, overdue: 'Overdue', duePending: 'Pending', stale: 'Stale', offline: 'Offline', withheld: 'Withheld', timeline: 'Timeline', concern: 'Concern' };
const fullEpisode = { episode_id: 'epi_one', revision: 1, state: 'open', urgency_state: 'none', origin_mode: 'SIM', concern_text: 'token', followup_tasks: [], withheld_answer_count: 0, allowed_actions: ['acknowledge', 'record_review'] };
test('lost review response retries the same identity and refreshes coherent history', async () => {
  const { EpisodePanel } = await import('./episode-panel.ts');
  const oldDocument = globalThis.document; globalThis.document = { createElement: () => new NodeStub() };
  const keys = []; let getCount = 0; let timelineCount = 0; let keyCount = 0;
  const transport = {
    get: async () => { getCount++; return fullEpisode; },
    timeline: async () => { timelineCount++; return { revision: 1, items: [] }; },
    command: async () => fullEpisode,
    review: async (path, body, revision, key) => { keys.push(key); if (keys.length === 1) throw new Error('lost response after commit'); return { review_id: 'rev_one' }; },
  };
  try {
    const panel = new EpisodePanel(new NodeStub(), transport, fullWords, () => `cmd_${++keyCount}`, async () => ({ decisions: [], unresolved: [] }));
    await panel.open('epi_one'); await panel.act('record_review'); await panel.retryCommand();
    assert.deepEqual(keys, ['cmd_1', 'cmd_1']); assert.equal(keyCount, 1);
    assert.equal(getCount, 2); assert.equal(timelineCount, 2);
  } finally { globalThis.document = oldDocument; }
});

test('stale view while editing never submits the completed form', async () => {
  const { EpisodePanel } = await import('./episode-panel.ts');
  const oldDocument = globalThis.document; globalThis.document = { createElement: tag => new NodeStub() };
  let finish; let commands = 0;
  const transport = { get: async () => fullEpisode, command: async () => { commands++; return fullEpisode; } };
  const panel = new EpisodePanel(new NodeStub(), transport, fullWords, () => 'cmd_one', () => new Promise(resolve => { finish = resolve; }));
  try {
    await panel.open('epi_one');
    const pending = panel.act('acknowledge');
    panel.streamLost(); finish({}); await pending;
    assert.equal(commands, 0);
  } finally { globalThis.document = oldDocument; }
});

test('paged history appends once and a changed revision disables further actions', async () => {
  const { EpisodePanel } = await import('./episode-panel.ts');
  const oldDocument = globalThis.document; globalThis.document = { createElement: () => new NodeStub() };
  let page = 0;
  const transport = {get: async () => fullEpisode, command: async () => fullEpisode, timeline: async path => {
    if (!path.includes('?')) return {revision:1,items:[{kind:'created',occurred_at:'t1',state:null,actor:null}],next_cursor:'1'};
    page++; return {revision:page === 1 ? 1 : 2,items:[{kind:'answer',occurred_at:'t2',state:null,actor:null}],next_cursor:page===1?'2':null};
  }};
  const root=new NodeStub();
  const panel=new EpisodePanel(root,transport,fullWords,()=> 'cmd_page',async()=>({}));
  const text=node=>[node.textContent??'',...(node.children??[]).map(text)].join(' ');
  try {
    await panel.open('epi_one');await panel.more('timeline');assert.equal(page,1);assert.match(text(root),/t1/);assert.match(text(root),/t2/);
    await panel.more('timeline');assert.match(text(root),/Stale/);
  } finally {globalThis.document=oldDocument;}
});

test('missing required unknown words refuse the episode surface instead of rendering a blank fallback', async () => {
  const { EpisodePanel } = await import('./episode-panel.ts');
  assert.throws(() => new EpisodePanel(new NodeStub(), {get: async () => fullEpisode, command: async () => fullEpisode}, {...fullWords,errors:{}}, () => 'command_words', async () => ({})), /episode words unavailable/);
});
