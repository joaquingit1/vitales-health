<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { CHROME } from "../../design/chrome";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import {
  STATE_MK,
  chance,
  loadEstimates,
  loadLots,
  percent,
  traceValue,
  words,
  type Estimate,
  type EstimatesView,
  type LotAssessment,
  type LotState,
  type LotsView,
  type Pathway,
} from "./medicines";

const api = useCrewApi();
const stage = useStage();
const session = useSession();
const lots = ref<LotsView | null>(null);
const estimates = ref<EstimatesView | null>(null);
const failed = ref(false);
const selected = ref<string | null>(null);
const ingredient = ref(0);

const KELVIN = 273.15;
const DAY_S = 86400;

function m(key: string, vars: Record<string, string | number> = {}): string {
  return copy(`medicine_words.${key}`, vars);
}

const byLot = computed(() => {
  const map = new Map<string, LotAssessment>();
  for (const lot of estimates.value?.lots ?? []) {
    map.set(lot.lot_id, lot);
  }
  return map;
});

function stateOf(lotId: string, fallback: string | null | undefined): LotState | null {
  const decided = byLot.value.get(lotId)?.decision.state;
  if (decided !== undefined) {
    return decided;
  }
  return fallback === "in_specification" || fallback === "adjust" || fallback === "do_not_use" ? fallback : null;
}

function stateWord(state: LotState | null): string {
  return m(state === null ? "state_none" : `state_${state}`);
}

const rows = computed(() =>
  (lots.value?.lots ?? []).map((lot) => {
    const assessed = byLot.value.get(lot.lot_id);
    const first = assessed?.estimates[0];
    return {
      lot,
      state: stateOf(lot.lot_id, lot.state),
      mean: first === undefined ? null : first.mean,
      count: assessed?.estimates.length ?? 0,
    };
  }),
);

const current = computed(() => rows.value.find((row) => row.lot.lot_id === selected.value) ?? rows.value[0] ?? null);
const assessment = computed(() => (current.value === null ? null : (byLot.value.get(current.value.lot.lot_id) ?? null)));
const estimate = computed((): Estimate | null => assessment.value?.estimates[ingredient.value] ?? assessment.value?.estimates[0] ?? null);

function choose(lotId: string): void {
  selected.value = lotId;
  ingredient.value = 0;
}

/* the potency scale: label claim at 100 %, the specification band, the coverage interval and the mean */
const scale = computed(() => {
  const e = estimate.value;
  if (e === null) {
    return null;
  }
  const low = Math.min(e.coverage_interval.low, e.specification.lower_percent) - 5;
  const high = Math.max(e.coverage_interval.high, e.specification.upper_percent) + 2;
  const at = (value: number): string => `${(((value - low) / (high - low)) * 100).toFixed(2)}%`;
  const width = (a: number, b: number): string => `${(((b - a) / (high - low)) * 100).toFixed(2)}%`;
  return {
    spec: { left: at(e.specification.lower_percent), width: width(e.specification.lower_percent, e.specification.upper_percent) },
    interval: { left: at(e.coverage_interval.low), width: width(e.coverage_interval.low, e.coverage_interval.high) },
    mean: at(e.mean),
    lower: at(e.specification.lower_percent),
  };
});

function pathwayName(pathway: Pathway): string {
  const tail = pathway.pathway_id.includes(":") ? pathway.pathway_id.slice(pathway.pathway_id.indexOf(":") + 1) : pathway.pathway_id;
  return words(tail);
}

const pathways = computed(() =>
  [...(estimate.value?.pathway_contributions ?? [])].sort((a, b) => b.share_nominal - a.share_nominal),
);

function celsius(kelvin: number | null): string {
  return kelvin === null ? copy("designed_states.no_reading") : `${(kelvin - KELVIN).toFixed(1)} °C`;
}

const storage = computed(() => {
  const e = estimate.value;
  if (e === null) {
    return [];
  }
  const x = e.exposure;
  return [
    { label: m("mkt"), value: celsius(x.mkt_k) },
    { label: m("temperature_range"), value: m("range", { low: celsius(x.min_temperature_k), high: celsius(x.max_temperature_k) }) },
    { label: m("dose"), value: `${(x.cumulative_absorbed_dose_gy * 1000).toFixed(1)} mGy` },
    { label: m("longest_gap"), value: `${(x.longest_gap_s / DAY_S).toFixed(1)} d` },
  ];
});

const radiation = computed(() => {
  const r = estimate.value?.radiation;
  if (r === undefined) {
    return [];
  }
  return [
    { label: m("radiation_method"), value: words(r.method) },
    { label: m("radiation_loss"), value: `${(r.central_loss_fraction_median * 100).toExponential(1)} %` },
    { label: m("radiation_bound"), value: `${r.upper_bound_k_per_gy.toExponential(1)} /Gy` },
    {
      label: r.radiosensitive ? m("radiosensitive", { threshold: r.radiosensitive_threshold_gy }) : m("dose"),
      value: `${(r.cumulative_absorbed_dose_gy * 1000).toFixed(1)} mGy`,
    },
  ];
});

/* the configured inputs and the computed rate basis the engine traced, without its bookkeeping rows */
const trace = computed(() =>
  (estimate.value?.trace ?? []).filter(
    (entry) => entry.value !== null && !entry.quantity.startsWith("degradant_") && !entry.quantity.startsWith("compression_"),
  ),
);

const layers = computed(() => {
  const seen = new Map<string, string>();
  for (const pathway of pathways.value) {
    for (const layer of (pathway as Pathway & { parameter_layers?: { name: string; method: string }[] }).parameter_layers ?? []) {
      seen.set(`${pathwayName(pathway)} · ${layer.name}`, layer.method);
    }
  }
  return [...seen.entries()].map(([name, method]) => ({ name, method: words(method) }));
});

const reasons = computed(() => {
  const list = assessment.value?.decision.reasons ?? [];
  const seen = new Set<string>();
  return list.filter((reason) => {
    const key = `${reason.severity}:${reason.message}`;
    if (seen.has(key) || reason.severity === "info") {
      return false;
    }
    seen.add(key);
    return true;
  });
});

function shortTime(iso: string | null): string {
  if (iso === null) {
    return "";
  }
  return new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

onMounted(async () => {
  const signedIn = session.state.value.session;
  stage.subline = signedIn?.person?.display_name ?? signedIn?.actor.display_name ?? "";
  stage.headline = m("headline");
  try {
    const [view, engine] = await Promise.all([loadLots(api), loadEstimates(api)]);
    lots.value = view;
    estimates.value = engine;
    stage.headline = m("headline"); /* again once loaded: a slower screen left behind may have written its own */
  } catch {
    failed.value = true;
  }
});
</script>

<template>
  <section
    class="cards flow f-med"
    data-view="medicines"
    :aria-label="m('title')"
  >
    <article class="card paper m-list">
      <div class="card-head">
        <h2 class="card-title">
          {{ m("title") }}
        </h2>
        <SourceWord
          v-if="lots?.assessment"
          :origin="lots.assessment.origin_mode"
        />
        <RouterLink
          class="go"
          :to="{ name: 'kit' }"
          :aria-label="CHROME.thisKit"
        >
          <svg
            class="icon"
            viewBox="0 0 24 24"
          ><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>
        </RouterLink>
      </div>
      <p class="sub">
        {{ m("intro") }}
      </p>
      <p
        v-if="estimates?.as_of"
        class="fine"
        data-testid="medicines-as-of"
      >
        {{ m("as_of", { time: shortTime(estimates.as_of) }) }} · {{ m("engine_version", { version: estimates.engine_version ?? "" }) }}
      </p>
      <p
        v-else-if="lots && !failed"
        class="fine"
      >
        {{ m("no_assessment") }}
      </p>
      <p
        v-if="lots?.assessment?.status === 'unknown'"
        class="fine"
      >
        {{ m("assessment_unknown") }}
      </p>
      <div class="flow-body">
        <div
          v-if="lots && lots.lots.length > 0"
          class="list"
          data-testid="medicine-lots"
        >
          <button
            v-for="row in rows"
            :key="row.lot.lot_id"
            type="button"
            class="li lot-row"
            :aria-pressed="current?.lot.lot_id === row.lot.lot_id"
            :data-lot="row.lot.lot_id"
            @click="choose(row.lot.lot_id)"
          >
            <span
              class="mk"
              :class="row.state === null ? 'none' : STATE_MK[row.state]"
            />
            <span class="what">{{ row.lot.name }}<small>{{ m("lot", { lot: row.lot.lot_id }) }} · {{ m("aboard", { n: row.lot.quantity_now.value }) }}</small></span>
            <span class="end"><b v-if="row.mean !== null">{{ percent(row.mean) }}%</b>{{ stateWord(row.state) }}</span>
          </button>
        </div>
        <p
          v-else-if="lots"
          class="kit-note"
        >
          {{ m("no_lots") }}
        </p>
        <p
          v-else-if="failed"
          class="kit-note"
        >
          {{ copy("designed_states.not_available_here") }}
        </p>
        <template v-if="current && reasons.length > 0">
          <h3 class="minor">
            {{ m("reasons") }} · {{ current.lot.name }}
          </h3>
          <ul
            class="reasons"
            data-testid="medicine-reasons"
          >
            <li
              v-for="reason in reasons"
              :key="reason.code + reason.message"
              :class="reason.severity"
            >
              {{ reason.message }}
            </li>
          </ul>
        </template>
      </div>
    </article>

    <article
      class="card g g-deep m-potency"
      data-testid="medicine-potency"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ current?.lot.name ?? m("predicted") }}
        </h2>
        <span
          v-if="current"
          class="tag state-tag"
          :class="current.state ?? 'none'"
        >{{ stateWord(current.state) }}</span>
      </div>
      <div
        v-if="assessment && assessment.estimates.length > 1"
        class="pills ingredient-pills"
      >
        <button
          v-for="(item, index) in assessment.estimates"
          :key="item.estimate_id"
          type="button"
          class="pill"
          :aria-pressed="index === ingredient"
          @click="ingredient = index"
        >
          {{ words(item.ingredient_id) }}
        </button>
      </div>
      <template v-if="estimate && scale">
        <div class="split">
          <div class="big">
            {{ percent(estimate.mean) }}<small>%</small>
          </div>
          <div class="p-below">
            <b>{{ chance(estimate.p_below_lower) }}%</b>
            <span>{{ m("below_lower") }}</span>
          </div>
        </div>
        <p class="sub">
          {{ m("predicted") }} {{ words(estimate.ingredient_id).toLowerCase() }} ·
          {{ m("interval", { p: Math.round(estimate.coverage_interval.probability * 100), low: percent(estimate.coverage_interval.low), high: percent(estimate.coverage_interval.high) }) }}
        </p>
        <div
          class="potency-scale"
          aria-hidden="true"
        >
          <i
            class="spec"
            :style="{ left: scale.spec.left, width: scale.spec.width }"
          />
          <i
            class="interval"
            :style="{ left: scale.interval.left, width: scale.interval.width }"
          />
          <i
            class="lower"
            :style="{ left: scale.lower }"
          />
          <i
            class="mean"
            :style="{ left: scale.mean }"
          />
        </div>
        <p class="fine">
          {{ m("specification", { lower: estimate.specification.lower_percent, upper: estimate.specification.upper_percent }) }} ·
          {{ m(`basis_${estimate.estimate_basis}`) }} ·
          {{ m("draws", { n: estimate.monte_carlo.n_draws }) }}, {{ estimate.monte_carlo.converged ? m("converged") : m("not_converged") }}
        </p>
        <div
          v-if="estimate.flags.length > 0"
          class="pills flag-pills"
          data-testid="medicine-flags"
        >
          <span
            v-for="flag in estimate.flags"
            :key="flag"
            class="pill"
          >{{ m(`flag_${flag}`) }}</span>
        </div>
      </template>
      <p
        v-else-if="current"
        class="sub"
      >
        {{ m("no_assessment") }}
      </p>
    </article>

    <article
      v-if="estimate"
      class="card paper m-path"
      data-testid="medicine-pathways"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ m("pathways") }}
        </h2>
      </div>
      <div class="flow-body">
        <div class="list">
          <div
            v-for="pathway in pathways"
            :key="pathway.pathway_id"
            class="li path-row"
            :class="{ quiet: !pathway.contributes }"
          >
            <span class="what">{{ pathwayName(pathway) }}<small>{{ m(`driver_${pathway.driver}`) }} · {{ m(`basis_${pathway.layer}`) }}<template v-if="!pathway.contributes"> · {{ m("not_contributing") }}</template></small></span>
            <span
              class="share"
              aria-hidden="true"
            ><i :style="{ width: `${(pathway.share_nominal * 100).toFixed(1)}%` }" /></span>
            <span class="end">{{ (pathway.share_nominal * 100).toFixed(0) }}%</span>
          </div>
        </div>
        <h3
          v-if="estimate.degradants.length > 0"
          class="minor"
        >
          {{ m("degradants") }}
        </h3>
        <div class="list">
          <div
            v-for="item in estimate.degradants"
            :key="item.degradant_id"
            class="li"
          >
            <span class="what">{{ item.name }}<small v-if="item.limit_percent !== null">{{ m("degradant_limit", { limit: item.limit_percent }) }}</small></span>
            <span class="end">{{ percent(item.mean, 2) }}%</span>
          </div>
        </div>
      </div>
    </article>

    <article
      v-if="estimate"
      class="card paper m-chem"
      data-testid="medicine-chemistry"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ m("chemistry") }}
        </h2>
      </div>
      <div class="flow-body chem-body">
        <div class="chem-col">
          <h3 class="minor">
            {{ m("storage") }}
          </h3>
          <dl class="facts">
            <template
              v-for="fact in storage"
              :key="fact.label"
            >
              <dt>{{ fact.label }}</dt>
              <dd>{{ fact.value }}</dd>
            </template>
          </dl>
          <h3 class="minor">
            {{ m("radiation") }}
          </h3>
          <dl class="facts">
            <template
              v-for="fact in radiation"
              :key="fact.label"
            >
              <dt>{{ fact.label }}</dt>
              <dd>{{ fact.value }}</dd>
            </template>
          </dl>
          <template v-if="estimate.placards.length > 0">
            <h3 class="minor">
              {{ m("placards") }}
            </h3>
            <p
              v-for="placard in estimate.placards"
              :key="placard.kind"
              class="placard"
            >
              {{ placard.message }}
            </p>
          </template>
        </div>
        <div class="chem-col">
          <h3 class="minor">
            {{ m("pathways") }}
          </h3>
          <dl class="facts">
            <template
              v-for="layer in layers"
              :key="layer.name"
            >
              <dt>{{ layer.name }}</dt>
              <dd>{{ layer.method }}</dd>
            </template>
          </dl>
          <dl class="facts trace">
            <template
              v-for="entry in trace"
              :key="entry.quantity"
            >
              <dt :title="entry.note">
                {{ words(entry.quantity) }}
              </dt>
              <dd :title="entry.origin">
                {{ traceValue(entry) }}
              </dd>
            </template>
          </dl>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.f-med .m-list { grid-column: 1 / span 10; grid-row: 1 / span 3; }
.f-med .m-potency { grid-column: 11 / span 14; grid-row: 1; }
.f-med .m-path { grid-column: 11 / span 6; grid-row: 2 / span 2; }
.f-med .m-chem { grid-column: 17 / span 8; grid-row: 2 / span 2; }
.go { color: inherit; }
.flow-body { display: flex; flex-direction: column; min-height: 0; flex: 1; overflow: auto; }
.lot-row { width: 100%; border: 0; border-top: 1px solid var(--line); background: none; text-align: left; font: inherit; color: inherit; cursor: pointer; padding: 12px 10px; margin: 0 -10px; width: calc(100% + 20px); border-radius: 14px; transition: background-color 160ms var(--ease); }
.lot-row:first-child { border-top-color: transparent; }
.lot-row:hover { background: rgba(28, 30, 28, 0.04); }
.lot-row[aria-pressed="true"] { background: rgba(226, 245, 92, 0.32); border-top-color: transparent; }
.lot-row[aria-pressed="true"] + .lot-row { border-top-color: transparent; }
.lot-row .end { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.lot-row .end b { font-size: 17px; line-height: 20px; font-weight: 500; color: var(--ink); font-variant-numeric: tabular-nums; }
.mk.stop { background: var(--ink); box-shadow: 0 0 0 3px rgba(28, 30, 28, 0.14); }
.m-potency .big { margin-top: 4px; }
.m-potency .split { align-items: flex-end; }
.p-below { display: flex; flex-direction: column; align-items: flex-end; padding-bottom: 8px; text-align: right; }
.p-below b { font-size: 28px; line-height: 32px; font-weight: 400; font-variant-numeric: tabular-nums; }
.p-below span { font-size: 13px; line-height: 18px; color: rgba(255, 255, 255, 0.78); }
.ingredient-pills { margin-top: 8px; }
.state-tag { background: rgba(255, 255, 255, 0.16); color: #fff; }
.state-tag.in_specification { background: var(--lime); color: var(--ink); }
.state-tag.do_not_use { background: #fff; color: var(--ink); }
.m-potency > * { flex: none; }
.potency-scale { position: relative; flex: none; height: 18px; margin-top: 12px; border-radius: 9px; background: rgba(255, 255, 255, 0.1); }
.potency-scale i { position: absolute; top: 0; bottom: 0; }
.potency-scale .spec { border-radius: 9px; background: rgba(226, 245, 92, 0.22); box-shadow: inset 0 0 0 1px rgba(226, 245, 92, 0.6); }
.potency-scale .interval { top: 5px; bottom: 5px; border-radius: 4px; background: rgba(255, 255, 255, 0.55); }
.potency-scale .lower { width: 2px; margin-left: -1px; background: var(--lime); }
.potency-scale .mean { top: -4px; bottom: -4px; width: 4px; margin-left: -2px; border-radius: 2px; background: #fff; box-shadow: 0 0 0 3px rgba(17, 51, 36, 0.6); }
.flag-pills { margin-top: 10px; gap: 6px; }
.flag-pills .pill { height: 24px; padding: 0 10px; display: inline-flex; align-items: center; font-size: 12px; }
.reasons { margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.reasons li { position: relative; padding-left: 16px; font-size: 14px; line-height: 20px; color: var(--ink-2); }
.reasons li::before { content: ""; position: absolute; left: 0; top: 6px; width: 8px; height: 8px; border-radius: 50%; box-shadow: inset 0 0 0 1.5px var(--ink-3); }
.reasons li.blocking::before { background: var(--ink); box-shadow: none; }
.path-row { gap: 10px; }
.path-row.quiet { opacity: 0.55; }
.path-row .what { flex: 1 1 auto; }
.share { flex: none; width: 64px; height: 6px; border-radius: 3px; background: var(--tile); overflow: hidden; }
.share i { display: block; height: 100%; border-radius: 3px; background: var(--green-2); }
.path-row .end { min-width: 34px; text-align: right; font-variant-numeric: tabular-nums; }
.minor { margin-top: 14px; font-size: 13px; line-height: 18px; font-weight: 500; color: var(--ink-2); }
.chem-body { display: block; }
.chem-col { min-width: 0; }
.facts { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; margin: 6px 0 0; font-size: 13px; line-height: 18px; }
.facts dt { min-width: 0; color: var(--ink-3); }
.facts dd { margin: 0; max-width: 220px; text-align: right; font-family: var(--mono); font-size: 12px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.facts.trace { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); }
.placard { margin: 4px 0 0; font-size: 12px; line-height: 16px; color: var(--ink-2); }
</style>
