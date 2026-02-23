/* eslint-disable no-undef */
/* Browserless DOM harness for dashboard inline script behavior checks. */
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const html = fs.readFileSync("dashboard/index.html", "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (scripts.length === 0) {
  throw new Error("No inline script found in dashboard/index.html");
}
const script = scripts[scripts.length - 1][1];

class ClassList {
  constructor() {
    this._classes = new Set();
  }
  add(name) {
    this._classes.add(name);
  }
  remove(name) {
    this._classes.delete(name);
  }
  contains(name) {
    return this._classes.has(name);
  }
  toggle(name, force) {
    if (force === undefined) {
      if (this._classes.has(name)) {
        this._classes.delete(name);
        return false;
      }
      this._classes.add(name);
      return true;
    }
    if (force) this._classes.add(name);
    else this._classes.delete(name);
    return force;
  }
}

function createElement(id) {
  return {
    id,
    style: {},
    classList: new ClassList(),
    addEventListener: () => {},
    parentElement: { innerHTML: "" },
    textContent: "",
    getContext: () => ({}),
    resize: () => {},
  };
}

function createWrapper(name) {
  return {
    dataset: { chart: name },
    classList: new ClassList(),
  };
}

const elements = new Map();
["chart-agent", "chart-model", "chart-tools"].forEach((id) => {
  elements.set(id, createElement(id));
});

const wrappers = new Map();
["agent", "model", "tools"].forEach((name) => {
  wrappers.set(name, createWrapper(name));
});

const documentStub = {
  getElementById: (id) => elements.get(id) || null,
  addEventListener: () => {},
  querySelector: (selector) => {
    const match = selector.match(/\[data-chart=\"(.*)\"\]/);
    if (!match) return null;
    return wrappers.get(match[1]) || null;
  },
  querySelectorAll: () => [],
};

const echarts = {
  init: () => ({
    setOption: () => {},
    resize: () => {},
    dispose: () => {},
  }),
};

// Alpine.js store stub for testing
const alpineStores = {
  app: {
    activeTab: "overview",
    filters: { agent: "all", scope: "week" },
    loading: { stats: false, runs: false, transcript: false },
  },
  stats: {
    totals: { runs: 0, messages: 0, tool_calls: 0 },
    derived: { avg_session_duration_ms: 0, avg_messages_per_session: 0, error_rate: 0 },
    by_agent: {},
    model_usage: {},
    tool_usage: {},
    daily_activity: [],
    hourly_activity: [],
  },
};

const Alpine = {
  store(name) {
    return alpineStores[name];
  },
};

const context = {
  console,
  document: documentStub,
  window: null,
  Alpine,
  echarts,
  fetch: () => Promise.reject(new Error("fetch not stubbed")),
};
context.window = context;

vm.createContext(context);
vm.runInContext(script, context);

function setStats(stats) {
  Object.assign(alpineStores.stats, {
    by_agent: stats.by_agent || {},
    model_usage: stats.model_usage || {},
    tool_usage: stats.tool_usage || {},
    daily_activity: stats.daily_activity || [],
    hourly_activity: stats.hourly_activity || [],
  });
}

// Empty data -> empty states
setStats({
  by_agent: { claude: { runs: 0 }, codex: { runs: 0 }, opencode: { runs: 0 } },
  model_usage: {},
  tool_usage: {},
});

context.renderAgentChart(alpineStores.stats.by_agent);
context.renderModelChart(alpineStores.stats.model_usage);
context.renderToolChart(alpineStores.stats.tool_usage);

assert.strictEqual(wrappers.get("agent").classList.contains("is-empty"), true, "Agent chart should show empty state");
assert.strictEqual(wrappers.get("model").classList.contains("is-empty"), true, "Model chart should show empty state");
assert.strictEqual(wrappers.get("tools").classList.contains("is-empty"), true, "Tool chart should show empty state");

// With data -> no empty states
setStats({
  by_agent: { claude: { runs: 1 }, codex: { runs: 0 }, opencode: { runs: 0 } },
  model_usage: { "model-1": { total: 10, input: 4, output: 6, calls: 1 } },
  tool_usage: { tool: 2 },
});

context.renderAgentChart(alpineStores.stats.by_agent);
context.renderModelChart(alpineStores.stats.model_usage);
context.renderToolChart(alpineStores.stats.tool_usage);

assert.strictEqual(wrappers.get("agent").classList.contains("is-empty"), false, "Agent chart should not show empty state");
assert.strictEqual(wrappers.get("model").classList.contains("is-empty"), false, "Model chart should not show empty state");
assert.strictEqual(wrappers.get("tools").classList.contains("is-empty"), false, "Tool chart should not show empty state");

console.log("js_render_harness: ok");
