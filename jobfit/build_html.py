"""Render jobs_v2.json into a single static, searchable HTML file (no server needed)."""

import json
from datetime import datetime, timezone

from jobfit import config

PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Fit v2 (Dan Hanegbi)</title>
<style>
  :root {
    --bg: #0b0e11; --panel: #12161b; --panel2: #171c22; --border: #232a32;
    --text: #e6e9ec; --text-dim: #9aa4ad; --accent: #76b900; --accent-dim: #4d7a00;
    --chip-bg: #1d2b12; --chip-border: #3a5a16; --danger: #ff6b6b; --danger-bg: #2a1414;
    --danger-border: #4a2323; --warn: #e0b34d; --warn-bg: #2a2414; --warn-border: #4a3f23;
    --blue: #8fc4e8; --blue-bg: #14202a; --blue-border: #274056;
    --purple: #c79bf0; --purple-bg: #211a2a; --purple-border: #402f56;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .app { display: grid; grid-template-columns: 320px 1fr; height: 100vh; }
  @media (max-width: 900px) { .app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: auto; } }

  .sidebar { background: var(--panel); border-right: 1px solid var(--border); padding: 20px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 18px; }
  .brand { display: flex; align-items: baseline; gap: 8px; }
  .brand h1 { font-size: 16px; margin: 0; font-weight: 700; }
  .brand .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); flex-shrink: 0; }
  .subtitle { color: var(--text-dim); font-size: 12px; line-height: 1.5; margin-top: -10px; }

  .field-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-dim); font-weight: 600; margin-bottom: 8px; }
  .field-label-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .field-label-row .field-label { margin-bottom: 0; }
  .field-label-btns { display: flex; gap: 6px; }
  .scope-toggle-btn.scope-narrow { color: var(--blue); border-color: var(--blue-border); background: var(--blue-bg); }
  .bulk-actions { display: flex; gap: 10px; flex-shrink: 0; }
  .bulk-btn { background: none; border: none; color: var(--accent); font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; cursor: pointer; padding: 0; }
  .mode-toggle-btn { background: var(--panel2); border: 1px solid var(--border); color: var(--text-dim); font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; cursor: pointer; padding: 2px 8px; border-radius: 999px; }
  .mode-toggle-btn.mode-and { color: var(--accent); border-color: var(--accent-dim); background: var(--chip-bg); }
  .save-filter-row { display: flex; gap: 6px; margin-bottom: 8px; }
  .save-filter-row input { flex: 1; min-width: 0; }
  .save-filter-row .btn { flex-shrink: 0; }
  .saved-filter-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .saved-filter-apply { flex: 1; min-width: 0; text-align: left; background: var(--panel2); border: 1px solid var(--border); color: var(--text); padding: 6px 10px; border-radius: 7px; font-size: 12.5px; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .saved-filter-apply:hover { border-color: var(--accent-dim); color: var(--accent); }
  .saved-filter-delete { flex-shrink: 0; background: none; border: 1px solid var(--border); color: var(--text-dim); cursor: pointer; font-size: 13px; line-height: 1; padding: 5px 9px; border-radius: 7px; }
  .saved-filter-delete:hover { color: var(--danger); border-color: var(--danger-border); background: var(--danger-bg); }
  .no-saved-filters { color: var(--text-dim); font-size: 12px; }
  .bulk-btn:hover { text-decoration: underline; }

  input[type="search"], input[type="text"] { width: 100%; background: var(--panel2); border: 1px solid var(--border); color: var(--text); padding: 9px 11px; border-radius: 8px; font-size: 13.5px; outline: none; }
  input[type="search"]:focus, input[type="text"]:focus { border-color: var(--accent-dim); }
  input[type="range"] { width: 100%; accent-color: var(--accent); }

  .checklist { display: flex; flex-direction: column; gap: 2px; max-height: 190px; overflow-y: auto; padding-right: 4px; border: 1px solid var(--border); border-radius: 8px; padding: 4px; }
  .checklist.checklist-lg { max-height: 260px; }
  .check-item { display: flex; align-items: center; gap: 8px; padding: 6px 7px; border-radius: 6px; cursor: pointer; font-size: 13px; user-select: none; }
  .check-item:hover { background: var(--panel2); }
  .check-item input { accent-color: var(--accent); cursor: pointer; width: 15px; height: 15px; flex-shrink: 0; }
  .check-item .label-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .check-item .only-btn { display: none; background: none; border: none; color: var(--accent); font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; cursor: pointer; padding: 2px 5px; flex-shrink: 0; }
  .check-item:hover .only-btn, .check-item:focus-within .only-btn { display: inline-block; }
  .check-item .count { color: var(--text-dim); font-size: 11.5px; flex-shrink: 0; }

  select { width: 100%; background: var(--panel2); border: 1px solid var(--border); color: var(--text); padding: 8px 10px; border-radius: 8px; font-size: 13px; }

  .toggle-row { display: flex; align-items: center; justify-content: space-between; font-size: 13px; padding: 2px 0; }
  .switch { position: relative; width: 36px; height: 20px; flex-shrink: 0; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider-track { position: absolute; inset: 0; background: var(--panel2); border: 1px solid var(--border); border-radius: 20px; cursor: pointer; transition: 0.15s; }
  .slider-track:before { content: ""; position: absolute; width: 14px; height: 14px; left: 2px; top: 2px; background: var(--text-dim); border-radius: 50%; transition: 0.15s; }
  .switch input:checked + .slider-track { background: var(--accent-dim); border-color: var(--accent); }
  .switch input:checked + .slider-track:before { transform: translateX(16px); background: #fff; }

  .btn { background: transparent; border: 1px solid var(--border); color: var(--text-dim); padding: 7px 11px; border-radius: 7px; font-size: 12px; cursor: pointer; }
  .btn:hover { color: var(--text); border-color: var(--text-dim); }
  .btn.primary { background: var(--accent-dim); border-color: var(--accent); color: #fff; }

  .stat-block { margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border); color: var(--text-dim); font-size: 11.5px; line-height: 1.6; }

  .main { display: flex; flex-direction: column; overflow: hidden; }
  .topbar { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; }
  .result-count { font-size: 13.5px; color: var(--text-dim); white-space: nowrap; }
  .result-count b { color: var(--text); }
  .quick-toggle-btn { background: var(--panel2); border: 1px solid var(--border); color: var(--text-dim); padding: 5px 10px; border-radius: 7px; font-size: 12px; cursor: pointer; }
  .quick-toggle-btn:hover { color: var(--text); border-color: var(--text-dim); }
  .quick-toggle-btn.active#qtLiked { color: #ff6b8a; border-color: #ff6b8a; background: #2a1420; }
  .quick-toggle-btn.active#qtHidden { color: var(--warn); border-color: var(--warn-border); background: var(--warn-bg); }
  .quick-toggle-btn.active#qtSent { color: var(--blue); border-color: var(--blue-border); background: var(--blue-bg); }
  .quick-toggle-btn.active#qtReached { color: var(--purple); border-color: var(--purple-border); background: var(--purple-bg); }
  .topbar-spacer { flex: 1; }

  .content { flex: 1; overflow-y: auto; padding: 18px 24px 48px; }
  .job-list { display: flex; flex-direction: column; gap: 10px; max-width: 960px; margin: 0 auto; }

  .company-group { width: 100%; margin: 0 0 18px; }
  .company-header { display: flex; align-items: center; gap: 10px; padding: 10px 4px; flex-wrap: wrap; }
  .company-header h3 { margin: 0; font-size: 15px; }
  .company-jobs { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }

  .job-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; transition: border-color 0.12s ease; display: flex; gap: 14px; }
  .job-card:hover { border-color: var(--accent-dim); }

  .score-badge { flex-shrink: 0; width: 52px; height: 52px; border-radius: 12px; display: flex; flex-direction: column; align-items: center; justify-content: center; font-weight: 800; font-size: 17px; line-height: 1; }
  .score-badge .cv { font-size: 8.5px; font-weight: 600; opacity: 0.8; margin-top: 2px; text-transform: uppercase; }
  .score-good { background: var(--chip-bg); border: 1px solid var(--chip-border); color: #b7e08a; }
  .score-mid { background: var(--warn-bg); border: 1px solid var(--warn-border); color: var(--warn); }
  .score-low { background: var(--danger-bg); border: 1px solid var(--danger-border); color: var(--danger); }

  .job-body { flex: 1; min-width: 0; }
  .job-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
  .job-title { font-size: 15px; font-weight: 600; margin: 0 0 3px; }
  .job-title-link { color: inherit; text-decoration: none; }
  .job-title-link:hover { color: var(--accent); text-decoration: underline; }
  .job-company { color: var(--text-dim); font-size: 12.5px; }
  .job-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
  .tag { background: var(--chip-bg); border: 1px solid var(--chip-border); color: #b7e08a; padding: 3px 9px; border-radius: 999px; font-size: 11px; white-space: nowrap; }
  .tag.muted { background: var(--panel2); border-color: var(--border); color: var(--text-dim); }
  .tag.conn { background: var(--blue-bg); border-color: var(--blue-border); color: var(--blue); }
  .tag.remote { background: #1a2a1a; border-color: #2f4a2f; color: #9fd18f; }
  .tag.lang { background: var(--blue-bg); border-color: var(--blue-border); color: var(--blue); font-weight: 600; }
  .tag.referral { background: #3a2a10; border-color: #6a4a18; color: #f0b95c; font-weight: 700; }
  .tag.new-job { background: var(--chip-bg); border-color: var(--accent); color: var(--accent); font-weight: 700; }
  .tag.closed-job { background: var(--danger-bg); border-color: var(--danger-border); color: var(--danger); }
  .suggest-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .suggest-chip { display: flex; align-items: center; gap: 4px; background: var(--panel2); border: 1px solid var(--border); border-radius: 999px; font-size: 11px; overflow: hidden; }
  .suggest-chip .word { background: none; border: none; color: var(--text-dim); padding: 4px 4px 4px 10px; cursor: pointer; font-size: 11px; }
  .suggest-chip .word:hover { color: var(--text); }
  .suggest-chip .dismiss { background: none; border: none; color: var(--text-dim); padding: 4px 9px 4px 4px; cursor: pointer; font-size: 11px; line-height: 1; opacity: 0.6; }
  .suggest-chip .dismiss:hover { opacity: 1; color: var(--danger); }
  .referral-contact { margin-top: 8px; font-size: 12.5px; color: #f0b95c; }
  .referral-contact b { color: var(--text); }
  .job-date { font-size: 11.5px; color: var(--text-dim); white-space: nowrap; flex-shrink: 0; text-align: right; }
  .job-head-right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0; }
  .card-actions { display: flex; gap: 4px; }
  .icon-btn { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--panel2); border: 1px solid var(--border); border-radius: 7px; color: var(--text-dim); font-size: 16px; line-height: 1; cursor: pointer; padding: 0; text-decoration: none; }
  .icon-btn:hover { color: var(--text); border-color: var(--text-dim); }
  .icon-btn.like-btn.active { color: #ff6b8a; border-color: #ff6b8a; background: #2a1420; }
  .icon-btn.hide-btn.active, .icon-btn.hide-company-btn.active { color: var(--warn); border-color: var(--warn-border); background: var(--warn-bg); }
  .icon-btn.sent-btn.active { color: var(--blue); border-color: var(--blue-border); background: var(--blue-bg); }
  .icon-btn.reached-btn.active { color: var(--purple); border-color: var(--purple-border); background: var(--purple-bg); }
  .icon-btn.open-btn { color: var(--accent); border-color: var(--accent-dim); }
  .icon-btn.open-btn:hover { background: var(--chip-bg); border-color: var(--accent); }
  .job-card.is-hidden { opacity: 0.55; }
  .hidden-banner { font-size: 11px; color: var(--warn); font-weight: 600; margin-top: 6px; }

  .score-both { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; align-items: center; }
  .cv-pill { display: inline-flex; align-items: baseline; gap: 5px; padding: 2px 8px; border-radius: 6px; background: var(--panel2); border: 1px solid var(--border); }
  .cv-pill .cv-name { font-size: 9px; text-transform: uppercase; letter-spacing: 0.4px; font-weight: 700; color: var(--text-dim); }
  .cv-pill .cv-score { font-size: 12.5px; font-weight: 700; color: var(--text); }
  .cv-pill.winner { border-color: var(--accent-dim); }
  .cv-pill.winner .cv-score { color: var(--accent); }
  .cv-conf { font-size: 10px; color: var(--text-dim); }
  .cv-conf.full { color: #b7e08a; }

  .job-detail { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border); font-size: 13px; line-height: 1.6; color: #d3d8dc; }
  .job-detail p { margin: 0 0 10px; white-space: pre-wrap; }
  .conn-buttons { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .conn-btn { display: inline-flex; align-items: center; gap: 5px; background: var(--blue-bg); border: 1px solid var(--blue-border); color: var(--blue); padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 600; text-decoration: none; cursor: pointer; transition: 0.12s; }
  .conn-btn:hover { background: #1c3346; border-color: #3a5a78; }
  .conn-btn.conn-btn-static { cursor: default; }

  .load-more { text-align: center; padding: 18px; }

  .empty-state { text-align: center; color: var(--text-dim); padding: 60px 20px; }
  ::-webkit-scrollbar { width: 9px; height: 9px; }
  ::-webkit-scrollbar-thumb { background: #2a323a; border-radius: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><span class="dot"></span><h1>Job Fit v2</h1></div>
    <div class="subtitle">Israel/remote software-adjacent openings from techmap-listed companies, cross-referenced with your LinkedIn connections. Score = % of the job's own stated requirements your CV covers, blended with title/role and experience fit — scored deterministically against two CVs (general AI/software vs. infra/MLOps), no AI calls, no per-job cost. Jobs without a real scraped description ("title-only") fall back to title/role + experience only — that's flagged on the card, not hidden.</div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Search</span>
        <span class="field-label-btns">
          <button type="button" class="mode-toggle-btn scope-toggle-btn" id="qScopeBtn" title="Click to cycle search scope: title + description, title only, or description only">Title+Desc</button>
          <button type="button" class="mode-toggle-btn" id="qModeBtn" title="Toggle whether comma-separated terms all need to match, or just one">OR</button>
        </span>
      </div>
      <input type="search" id="qSearch" placeholder="python, staff engineer">
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Exclude (title)</span>
        <button type="button" class="mode-toggle-btn" id="excludeModeBtn" title="Toggle whether comma-separated terms all need to match, or just one">OR</button>
      </div>
      <input type="search" id="qExcludeTitle" placeholder="senior, manager">
      <div class="suggest-row" id="excludeSuggestions"></div>
    </div>

    <div>
      <div class="field-label">Score against</div>
      <select id="cvSelect">
        <option value="best">Best of all CVs</option>
      </select>
    </div>

    <div>
      <div class="field-label">Min score <span id="minScoreVal" style="color:var(--accent)"></span></div>
      <input type="range" id="minScore" min="0" max="100" value="0">
    </div>

    <div class="toggle-row"><span>Remote only</span><label class="switch"><input type="checkbox" id="fRemote"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Has a connection</span><label class="switch"><input type="checkbox" id="fConn"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Has full description</span><label class="switch"><input type="checkbox" id="fDesc"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Liked only</span><label class="switch"><input type="checkbox" id="fLiked"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Show hidden jobs</span><label class="switch"><input type="checkbox" id="fShowHidden"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Show CV-sent jobs</span><label class="switch"><input type="checkbox" id="fShowSent"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Show reached-out jobs</span><label class="switch"><input type="checkbox" id="fShowReached"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Show closed jobs</span><label class="switch"><input type="checkbox" id="fShowClosed"><span class="slider-track"></span></label></div>
    <div class="toggle-row"><span>Referral only</span><label class="switch"><input type="checkbox" id="fReferral"><span class="slider-track"></span></label></div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Years of experience required</span>
        <span class="bulk-actions"><button type="button" class="bulk-btn" data-bulk="years" data-action="clear">Clear</button></span>
      </div>
      <div class="checklist" id="yearsList"></div>
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">City</span>
        <span class="bulk-actions"><button type="button" class="bulk-btn" data-bulk="city" data-action="clear">Clear</button></span>
      </div>
      <div class="checklist" id="cityList"></div>
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Language</span>
        <span class="bulk-actions"><button type="button" class="bulk-btn" data-bulk="language" data-action="clear">Clear</button></span>
      </div>
      <div class="checklist" id="languageList"></div>
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Company <span id="companySelCount" style="color:var(--accent)"></span></span>
        <span class="bulk-actions">
          <button type="button" class="bulk-btn" data-bulk="company" data-action="all">All</button>
          <button type="button" class="bulk-btn" data-bulk="company" data-action="none">None</button>
        </span>
      </div>
      <input type="search" class="loc-search" id="companySearch" placeholder="Filter companies..." style="margin-bottom:6px;">
      <div class="checklist checklist-lg" id="companyList"></div>
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Industry</span>
        <span class="bulk-actions"><button type="button" class="bulk-btn" data-bulk="industry" data-action="clear">Clear</button></span>
      </div>
      <div class="checklist" id="industryList"></div>
    </div>

    <div>
      <div class="field-label-row">
        <span class="field-label">Function</span>
        <span class="bulk-actions"><button type="button" class="bulk-btn" data-bulk="dept" data-action="clear">Clear</button></span>
      </div>
      <div class="checklist" id="deptList"></div>
    </div>

    <button class="btn" id="resetBtn">Reset filters</button>
    <button class="btn" id="clearDataBtn" title="If the view ever looks stuck or wrong, this wipes all saved filters/liked/hidden state and reloads">Clear saved data &amp; reload</button>

    <div>
      <div class="field-label">Saved filters</div>
      <div class="save-filter-row">
        <input type="text" id="saveFilterName" placeholder="Name this filter set...">
        <button type="button" class="btn" id="saveFilterBtn">Save</button>
      </div>
      <div id="savedFiltersList"></div>
    </div>

    <div class="stat-block" id="statBlock"></div>
  </aside>

  <main class="main">
    <div class="topbar">
      <div class="result-count" id="resultCount"></div>
      <button type="button" class="quick-toggle-btn" id="qtLiked" title="Show liked jobs only">♥ Liked</button>
      <button type="button" class="quick-toggle-btn" id="qtHidden" title="Show hidden jobs">✕ Hidden</button>
      <button type="button" class="quick-toggle-btn" id="qtSent" title="Show CV-sent jobs">➤ Sent</button>
      <button type="button" class="quick-toggle-btn" id="qtReached" title="Show reached-out jobs">☎ Reached</button>
      <div class="topbar-spacer"></div>
      <div class="toggle-row" style="gap:8px;"><span>Group by company</span><label class="switch"><input type="checkbox" id="groupToggle"><span class="slider-track"></span></label></div>
      <select id="sortSelect" style="width:auto;">
        <option value="score_desc">Best score first</option>
        <option value="date_desc">Most recently posted</option>
        <option value="company_asc">Company A-Z</option>
      </select>
    </div>
    <div class="content">
      <div class="job-list" id="jobList"></div>
      <div class="empty-state" id="emptyState" style="display:none;">No jobs match your filters.</div>
      <div class="load-more" id="loadMoreWrap" style="display:none;"><button class="btn" id="loadMoreBtn">Load more</button></div>
    </div>
  </main>
</div>

<script>
const JOBS = __JOBS_JSON__;
const PROFILES = __PROFILES_JSON__;
const GENERATED_AT = __GENERATED_AT_JSON__;

const cvSelectEl = document.getElementById("cvSelect");
for (const p of PROFILES) {
  const opt = document.createElement("option");
  opt.value = p.id;
  opt.textContent = p.name;
  cvSelectEl.appendChild(opt);
}

const PAGE_SIZE = 100;
let visibleCount = PAGE_SIZE;

const state = {
  q: "", qMode: "OR", qScope: "both", excludeTitle: "", excludeMode: "OR", cv: "best", minScore: 0, remote: false, conn: false, desc: false,
  companies: null, cities: new Set(), industries: new Set(), depts: new Set(), years: new Set(), languages: new Set(),
  group: false, sort: "score_desc", likedOnly: false, showHidden: false, showSent: false, showReached: false, referralOnly: false, showClosed: false,
};
const COMPANY_PAGE_SIZE = 8;
const expandedCompanies = new Set();

function loadIdSet(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch (e) {
    return new Set();
  }
}
function saveIdSet(key, set) {
  try {
    localStorage.setItem(key, JSON.stringify(Array.from(set)));
  } catch (e) { /* private mode / storage blocked - liked/hidden just won't persist */ }
}
const likedIds = loadIdSet("jobfit_liked");
const hiddenIds = loadIdSet("jobfit_hidden");
const sentIds = loadIdSet("jobfit_sent");
const reachedIds = loadIdSet("jobfit_reached");
const hiddenCompanies = loadIdSet("jobfit_hidden_companies");

function uniqueSorted(field) {
  return Array.from(new Set(JOBS.map(j => j[field]).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}
const allCompanies = uniqueSorted("company");
const allCities = uniqueSorted("city");
const allIndustries = uniqueSorted("industry");
const allDepts = uniqueSorted("department");
const YEARS_BUCKETS = ["1", "2", "3", "4", "5", "6", "7", "8", "9+", "Unspecified"];
state.companies = new Set(allCompanies);

const LANGUAGE_DEFS = [
  ["C++", /\bc\+\+/i],
  ["C#", /\bc#/i],
  ["Objective-C", /\bobjective-c\b/i],
  ["Python", /\bpython\b/i],
  ["JavaScript", /\bjavascript\b/i],
  ["TypeScript", /\btypescript\b/i],
  ["Java", /\bjava\b(?!\s*script)/i],
  ["Go", /\bgolang\b/i],
  ["Ruby", /\bruby\b/i],
  ["PHP", /\bphp\b/i],
  ["Swift", /\bswift\b/i],
  ["Kotlin", /\bkotlin\b/i],
  ["Rust", /\brust\b/i],
  ["Scala", /\bscala\b/i],
  ["Perl", /\bperl\b/i],
  ["MATLAB", /\bmatlab\b/i],
  ["SQL", /\bsql\b/i],
  ["Bash/Shell", /\bbash\b|\bshell scripting\b/i],
  // Bare "C" is ambiguous in prose, so it's matched case-sensitively as a lone
  // token not already part of "C++"/"C#" (matched above), and not "C-level"
  // or a "Series C" funding round.
  ["C", /(?<!Series )(?<![A-Za-z0-9#+.-])C(?![A-Za-z0-9#+&-])/],
];
function detectLanguages(job) {
  const text = job.title + " " + (job.description || "");
  const found = [];
  for (const [name, re] of LANGUAGE_DEFS) {
    if (re.test(text)) found.push(name);
  }
  return found;
}
JOBS.forEach(j => { j._languages = detectLanguages(j); });
const allLanguages = LANGUAGE_DEFS.map(d => d[0]).filter(name => JOBS.some(j => j._languages.includes(name)));

const TITLE_WORD_STOPWORDS = new Set([
  "and", "or", "the", "a", "an", "of", "for", "to", "in", "on", "at", "with", "&",
  "i", "ii", "iii", "iv", "v", "new", "team", "role",
]);
const dismissedSuggestions = loadIdSet("jobfit_dismissed_suggestions");
const titleWordCounts = (() => {
  const counts = new Map();
  for (const job of JOBS) {
    const words = new Set((job.title || "").toLowerCase().match(/[a-z][a-z0-9+#.-]*/g) || []);
    for (const w of words) {
      if (w.length < 3 || TITLE_WORD_STOPWORDS.has(w)) continue;
      counts.set(w, (counts.get(w) || 0) + 1);
    }
  }
  return counts;
})();
const rankedTitleWords = Array.from(titleWordCounts.entries()).sort((a, b) => b[1] - a[1]).map(e => e[0]);

function refreshExcludeSuggestions() {
  const current = new Set(parseTerms(state.excludeTitle));
  const words = rankedTitleWords.filter(w => !current.has(w) && !dismissedSuggestions.has(w)).slice(0, 15);
  const el = document.getElementById("excludeSuggestions");
  el.innerHTML = words.map(w => (
    `<span class="suggest-chip"><button type="button" class="word" data-word="${escapeHtml(w)}" title="Add to exclude">${escapeHtml(w)}</button>` +
    `<button type="button" class="dismiss" data-word="${escapeHtml(w)}" title="Remove from suggestions">&times;</button></span>`
  )).join("");
}
document.getElementById("excludeSuggestions").addEventListener("click", (e) => {
  const target = e.target.closest("button");
  if (!target) return;
  const word = target.dataset.word;
  if (target.classList.contains("word")) {
    const terms = parseTerms(state.excludeTitle);
    if (!terms.includes(word)) terms.push(word);
    state.excludeTitle = terms.join(", ");
    document.getElementById("qExcludeTitle").value = state.excludeTitle;
    refreshExcludeSuggestions();
    visibleCount = PAGE_SIZE;
    render();
  } else if (target.classList.contains("dismiss")) {
    dismissedSuggestions.add(word);
    saveIdSet("jobfit_dismissed_suggestions", dismissedSuggestions);
    refreshExcludeSuggestions();
  }
});

const QSCOPE_LABELS = { both: "Title+Desc", title: "Title only", description: "Desc only" };
const QSCOPE_CYCLE = { both: "title", title: "description", description: "both" };
function syncScopeBtn() {
  const btn = document.getElementById("qScopeBtn");
  btn.textContent = QSCOPE_LABELS[state.qScope];
  btn.classList.toggle("scope-narrow", state.qScope !== "both");
}

const FILTER_STATE_KEY = "jobfit_filters";
const SAVED_FILTERS_KEY = "jobfit_saved_filters";

function captureFilterSnapshot() {
  const allCompaniesSelected = state.companies.size >= allCompanies.length;
  return {
    q: state.q, qMode: state.qMode, qScope: state.qScope, excludeTitle: state.excludeTitle, excludeMode: state.excludeMode, cv: state.cv, minScore: state.minScore,
    remote: state.remote, conn: state.conn, desc: state.desc,
    companies: allCompaniesSelected ? null : Array.from(state.companies),
    cities: Array.from(state.cities), industries: Array.from(state.industries),
    depts: Array.from(state.depts), years: Array.from(state.years), languages: Array.from(state.languages),
    group: state.group, sort: state.sort,
    likedOnly: state.likedOnly, showHidden: state.showHidden, showSent: state.showSent, showReached: state.showReached, referralOnly: state.referralOnly,
    showClosed: state.showClosed,
  };
}

function saveFilterState() {
  try {
    localStorage.setItem(FILTER_STATE_KEY, JSON.stringify(captureFilterSnapshot()));
  } catch (e) { /* private mode / storage blocked - filters just won't persist */ }
}

function loadSavedFilters() {
  try {
    const raw = localStorage.getItem(SAVED_FILTERS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}
function persistSavedFilters(all) {
  try { localStorage.setItem(SAVED_FILTERS_KEY, JSON.stringify(all)); } catch (e) { /* private mode / storage blocked */ }
}
function applySavedFiltersByName(name) {
  const snap = loadSavedFilters()[name];
  if (!snap) return;
  applyFilterState(snap);
  setupCompanyList(document.getElementById("companySearch").value);
  refreshCityList();
  refreshIndustryList();
  refreshDeptList();
  refreshYearsList();
  refreshLanguageList();
  visibleCount = PAGE_SIZE;
  render();
}
function refreshSavedFiltersList() {
  const all = loadSavedFilters();
  const names = Object.keys(all).sort((a, b) => a.localeCompare(b));
  const el = document.getElementById("savedFiltersList");
  el.innerHTML = names.length ? names.map(name => `
    <div class="saved-filter-row">
      <button type="button" class="saved-filter-apply" data-name="${escapeHtml(name)}" title="Apply this saved filter">${escapeHtml(name)}</button>
      <button type="button" class="saved-filter-delete" data-name="${escapeHtml(name)}" title="Delete this saved filter">✕</button>
    </div>`).join("") : `<div class="no-saved-filters">No saved filters yet.</div>`;
  el.querySelectorAll(".saved-filter-apply").forEach(btn => {
    btn.addEventListener("click", () => applySavedFiltersByName(btn.dataset.name));
  });
  el.querySelectorAll(".saved-filter-delete").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const all2 = loadSavedFilters();
      delete all2[btn.dataset.name];
      persistSavedFilters(all2);
      refreshSavedFiltersList();
    });
  });
}

function loadFilterState() {
  try {
    const raw = localStorage.getItem(FILTER_STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function applyFilterState(saved) {
  if (!saved) return;
  state.q = saved.q || ""; state.qMode = saved.qMode === "AND" ? "AND" : "OR";
  state.qScope = ["title", "description"].includes(saved.qScope) ? saved.qScope : "both";
  state.excludeTitle = saved.excludeTitle || ""; state.excludeMode = saved.excludeMode === "AND" ? "AND" : "OR";
  state.cv = saved.cv || "best"; state.minScore = saved.minScore || 0;
  state.remote = !!saved.remote; state.conn = !!saved.conn; state.desc = !!saved.desc;
  state.group = !!saved.group; state.sort = saved.sort || "score_desc";
  state.likedOnly = !!saved.likedOnly; state.showHidden = !!saved.showHidden; state.showSent = !!saved.showSent; state.showReached = !!saved.showReached; state.referralOnly = !!saved.referralOnly;
  state.showClosed = !!saved.showClosed;
  // null means "all companies" (the default) - saved.companies is only ever a
  // real list when a subset was picked, and only the ones still in this
  // dataset are kept (a saved company from a prior data refresh may be gone).
  if (saved.companies) state.companies = new Set(saved.companies.filter(c => allCompanies.includes(c)));
  if (saved.cities) state.cities = new Set(saved.cities.filter(c => allCities.includes(c)));
  if (saved.industries) state.industries = new Set(saved.industries.filter(c => allIndustries.includes(c)));
  if (saved.depts) state.depts = new Set(saved.depts.filter(c => allDepts.includes(c)));
  if (saved.years) state.years = new Set(saved.years.filter(c => YEARS_BUCKETS.includes(c)));
  if (saved.languages) state.languages = new Set(saved.languages.filter(c => allLanguages.includes(c)));

  document.getElementById("qSearch").value = state.q;
  document.getElementById("qExcludeTitle").value = state.excludeTitle;
  syncModeBtn("qModeBtn", state.qMode);
  syncModeBtn("excludeModeBtn", state.excludeMode);
  syncScopeBtn();
  document.getElementById("cvSelect").value = state.cv;
  document.getElementById("minScore").value = state.minScore;
  document.getElementById("minScoreVal").textContent = state.minScore;
  document.getElementById("fRemote").checked = state.remote;
  document.getElementById("fConn").checked = state.conn;
  document.getElementById("fDesc").checked = state.desc;
  document.getElementById("fLiked").checked = state.likedOnly;
  document.getElementById("fShowHidden").checked = state.showHidden;
  document.getElementById("fShowSent").checked = state.showSent;
  document.getElementById("fShowReached").checked = state.showReached;
  document.getElementById("fReferral").checked = state.referralOnly;
  document.getElementById("fShowClosed").checked = state.showClosed;
  document.getElementById("groupToggle").checked = state.group;
  document.getElementById("sortSelect").value = state.sort;
}
applyFilterState(loadFilterState());

function yearsBucket(job) {
  const y = job.years_required;
  if (y == null) return "Unspecified";
  return y >= 9 ? "9+" : String(y);
}
JOBS.forEach(j => { j._yearsBucket = yearsBucket(j); });

function scoreFor(job) {
  if (state.cv === "best") return job.best_score;
  return job[`score_${state.cv}`];
}
function cvLabelFor(job) {
  if (state.cv === "best") return job.best_cv;
  return state.cv;
}
function profileName(id) {
  const p = PROFILES.find(p => p.id === id);
  return p ? p.name : id;
}

function shortDescription(text, limit) {
  limit = limit || 280;
  if (!text || text.length <= limit) return text || "";
  const cut = text.slice(0, limit);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut) + "…";
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
// Comma-separated multi-term input: "python, staff" -> ["python", "staff"].
function parseTerms(raw) {
  return (raw || "").split(",").map(t => t.trim().toLowerCase()).filter(Boolean);
}
function syncModeBtn(elId, mode) {
  const btn = document.getElementById(elId);
  btn.textContent = mode;
  btn.classList.toggle("mode-and", mode === "AND");
}
function firstMatchingTerm(text, terms) {
  const hay = (text || "").toLowerCase();
  return terms.find(t => hay.includes(t)) || "";
}
function highlight(text, term) {
  if (!term) return escapeHtml(text);
  const idx = text.toLowerCase().indexOf(term.toLowerCase());
  if (idx === -1) return escapeHtml(text);
  return escapeHtml(text.slice(0, idx)) + "<mark>" + escapeHtml(text.slice(idx, idx + term.length)) + "</mark>" + escapeHtml(text.slice(idx + term.length));
}

function matchesFilters(job) {
  const qTerms = parseTerms(state.q);
  if (qTerms.length) {
    const hay = (state.qScope === "title" ? job.title : state.qScope === "description" ? (job.description || "") : job.title + " " + (job.description || "")).toLowerCase();
    const matches = state.qMode === "AND" ? qTerms.every(t => hay.includes(t)) : qTerms.some(t => hay.includes(t));
    if (!matches) return false;
  }
  const excludeTerms = parseTerms(state.excludeTitle);
  if (excludeTerms.length) {
    const hay = job.title.toLowerCase();
    const matches = state.excludeMode === "AND" ? excludeTerms.every(t => hay.includes(t)) : excludeTerms.some(t => hay.includes(t));
    if (matches) return false;
  }
  if (scoreFor(job) < state.minScore) return false;
  if (state.remote && !job.is_remote) return false;
  if (state.conn && !job.has_connection) return false;
  if (state.desc && !job.has_description) return false;
  if (!state.companies.has(job.company)) return false;
  if (state.cities.size && (!job.city || !state.cities.has(job.city))) return false;
  if (state.industries.size && (!job.industry || !state.industries.has(job.industry))) return false;
  if (state.depts.size && (!job.department || !state.depts.has(job.department))) return false;
  if (state.years.size && !state.years.has(job._yearsBucket)) return false;
  if (state.languages.size && !job._languages.some(l => state.languages.has(l))) return false;
  if (!state.showHidden && (hiddenIds.has(job.id) || hiddenCompanies.has(job.company))) return false;
  if (!state.showSent && sentIds.has(job.id)) return false;
  if (!state.showReached && reachedIds.has(job.id)) return false;
  if (!state.showClosed && job.status === "closed") return false;
  if (state.likedOnly && !likedIds.has(job.id)) return false;
  if (state.referralOnly && !job.is_referral) return false;
  return true;
}

function sortJobs(jobs) {
  const arr = jobs.slice();
  if (state.sort === "score_desc") arr.sort((a, b) => scoreFor(b) - scoreFor(a));
  else if (state.sort === "date_desc") arr.sort((a, b) => (b.posted_at || "").localeCompare(a.posted_at || ""));
  else if (state.sort === "company_asc") arr.sort((a, b) => a.company.localeCompare(b.company) || (scoreFor(b) - scoreFor(a)));
  return arr;
}

function scoreClass(score) {
  if (score >= 60) return "score-good";
  if (score >= 30) return "score-mid";
  return "score-low";
}
function confidenceFor(job, cvName) {
  return job[`confidence_${cvName}`];
}

function jobMetaTags(job) {
  const tags = [];
  if (job.status === "new") tags.push(`<span class="tag new-job">New</span>`);
  if (job.status === "closed") tags.push(`<span class="tag closed-job">Closed</span>`);
  if (job.is_referral) tags.push(`<span class="tag referral">Referral</span>`);
  if (job.location) tags.push(`<span class="tag muted">${escapeHtml(job.location)}</span>`);
  if (job.is_remote) tags.push(`<span class="tag remote">remote</span>`);
  if (job.department) tags.push(`<span class="tag muted">${escapeHtml(job.department)}</span>`);
  if (job.employment_type) tags.push(`<span class="tag muted">${escapeHtml(job.employment_type)}</span>`);
  if (job.has_connection) tags.push(`<span class="tag conn">${job.connections.length} connection${job.connections.length > 1 ? "s" : ""}</span>`);
  for (const lang of job._languages) tags.push(`<span class="tag lang">${escapeHtml(lang)}</span>`);
  return tags.join("");
}

function cvPillHtml(name, label, score, confidence, coverage, isBest) {
  const confBadge = confidence === "title_only"
    ? `<span class="cv-conf" title="No full description to check requirements against - based on title/role/experience only">title only</span>`
    : (coverage != null ? `<span class="cv-conf full" title="% of this job's own listed requirements your CV covers">${coverage}% req match</span>` : "");
  return `<span class="cv-pill ${isBest ? "winner" : ""}" title="${isBest ? "Best-fit CV" : ""}">
    <span class="cv-name">${label}</span><span class="cv-score">${score}</span>${confBadge}
  </span>`;
}

function jobCardHtml(job, showCompany) {
  const score = scoreFor(job);
  const cls = scoreClass(score);
  const cv = cvLabelFor(job);
  const qTerms = parseTerms(state.q);
  const titleTerm = firstMatchingTerm(job.title, qTerms);
  const descTerm = firstMatchingTerm(job.description, qTerms);
  const matched = (state.cv === "best" ? job[`matched_${job.best_cv}`] : job[`matched_${state.cv}`]) || [];
  const skillChips = matched.filter(m => !m.startsWith("-") && !m.includes("(") && !m.includes(":")).slice(0, 10)
    .map(m => `<span class="tag">${escapeHtml(m)}</span>`).join("");
  const connButtons = (job.connections || []).map(c =>
    c.url
      ? `<a class="conn-btn" href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.name)}</a>`
      : `<span class="conn-btn conn-btn-static">${escapeHtml(c.name)}</span>`
  ).join("");
  const isLiked = likedIds.has(job.id);
  const isHidden = hiddenIds.has(job.id);
  const isSent = sentIds.has(job.id);
  const isReached = reachedIds.has(job.id);
  const isCompanyHidden = hiddenCompanies.has(job.company);
  let banner = "";
  if (isCompanyHidden) banner = `<div class="hidden-banner">Company hidden</div>`;
  else if (isHidden) banner = `<div class="hidden-banner">Hidden</div>`;
  return `
  <div class="job-card ${(isHidden || isCompanyHidden) ? "is-hidden" : ""}" data-id="${job.id}">
    <div class="score-badge ${cls}"><div>${score}</div><div class="cv">${escapeHtml(cv)}</div></div>
    <div class="job-body">
      <div class="job-head">
        <div>
          <div class="job-title">${job.url ? `<a class="job-title-link" href="${escapeHtml(job.url)}" target="_blank" rel="noopener" title="Open listing">${highlight(job.title, titleTerm)}</a>` : highlight(job.title, titleTerm)}</div>
          ${showCompany ? `<div class="job-company">${escapeHtml(job.company)}${job.company_size ? " &middot; " + escapeHtml(job.company_size) : ""}${job.industry ? " &middot; " + escapeHtml(job.industry) : ""}</div>` : ""}
        </div>
        <div class="job-head-right">
          <div class="job-date">${escapeHtml(job.posted_at || "")}</div>
          <div class="card-actions">
            <button class="icon-btn sent-btn ${isSent ? "active" : ""}" data-action="sent" data-id="${job.id}" title="${isSent ? "Mark CV as not sent" : "Mark CV as sent"}">➤</button>
            <button class="icon-btn reached-btn ${isReached ? "active" : ""}" data-action="reached" data-id="${job.id}" title="${isReached ? "Mark as not reached out" : "Mark as reached out"}">☎</button>
            <button class="icon-btn like-btn ${isLiked ? "active" : ""}" data-action="like" data-id="${job.id}" title="${isLiked ? "Unlike" : "Like"}">${isLiked ? "♥" : "♡"}</button>
            <button class="icon-btn hide-btn ${isHidden ? "active" : ""}" data-action="hide" data-id="${job.id}" title="${isHidden ? "Unhide job" : "Hide this job"}">${isHidden ? "↺" : "✕"}</button>
            <button class="icon-btn hide-company-btn ${isCompanyHidden ? "active" : ""}" data-action="hide-company" data-company="${escapeHtml(job.company)}" title="${isCompanyHidden ? "Unhide company" : "Hide all jobs from " + escapeHtml(job.company)}">${isCompanyHidden ? "↺" : "⊘"}</button>
            ${job.url ? `<a class="icon-btn open-btn" href="${escapeHtml(job.url)}" target="_blank" rel="noopener" title="Open listing">↗</a>` : ""}
          </div>
        </div>
      </div>
      ${banner}
      <div class="score-both">
        ${PROFILES.map(p => cvPillHtml(p.id, p.name, job[`score_${p.id}`], job[`confidence_${p.id}`], job[`coverage_${p.id}`], job.best_cv === p.id)).join("")}
      </div>
      <div class="job-meta">${jobMetaTags(job)}</div>
      <div class="job-detail">
        ${job.description ? `<p>${highlight(shortDescription(job.description), descTerm)}</p>` : `<p style="color:var(--text-dim);font-style:italic;">No full description available for this listing (source didn't expose one) — score is based on title/role/location.</p>`}
        ${skillChips ? `<div class="job-meta">${skillChips}</div>` : ""}
        ${connButtons ? `<div class="conn-buttons">${connButtons}</div>` : ""}
        ${job.referral_contact ? `<div class="referral-contact"><b>Referral contact:</b> ${escapeHtml(job.referral_contact)}</div>` : ""}
      </div>
    </div>
  </div>`;
}

function render() {
  saveFilterState();
  const filtered = sortJobs(JOBS.filter(matchesFilters));
  const companyCount = new Set(filtered.map(j => j.company)).size;
  document.getElementById("resultCount").innerHTML = `<b>${filtered.length}</b> job${filtered.length === 1 ? "" : "s"} match &middot; <b>${companyCount}</b> compan${companyCount === 1 ? "y" : "ies"}`;
  document.getElementById("qtLiked").classList.toggle("active", state.likedOnly);
  document.getElementById("qtHidden").classList.toggle("active", state.showHidden);
  document.getElementById("qtSent").classList.toggle("active", state.showSent);
  document.getElementById("qtReached").classList.toggle("active", state.showReached);
  const listEl = document.getElementById("jobList");
  const emptyEl = document.getElementById("emptyState");
  const loadMoreWrap = document.getElementById("loadMoreWrap");

  if (filtered.length === 0) {
    listEl.innerHTML = "";
    emptyEl.style.display = "block";
    loadMoreWrap.style.display = "none";
    return;
  }
  emptyEl.style.display = "none";

  if (state.group) {
    const groups = new Map();
    for (const job of filtered) {
      if (!groups.has(job.company)) groups.set(job.company, []);
      groups.get(job.company).push(job);
    }
    const companyOrder = Array.from(groups.keys()).sort((a, b) => {
      const bestA = Math.max(...groups.get(a).map(scoreFor));
      const bestB = Math.max(...groups.get(b).map(scoreFor));
      return bestB - bestA;
    });
    listEl.innerHTML = companyOrder.map(company => {
      const jobs = groups.get(company);
      const meta = jobs[0];
      const connBadge = meta.has_connection ? `<span class="tag conn">${meta.connections.length} connection${meta.connections.length > 1 ? "s" : ""}</span>` : "";
      const expanded = expandedCompanies.has(company);
      const shown = expanded ? jobs : jobs.slice(0, COMPANY_PAGE_SIZE);
      const remaining = jobs.length - shown.length;
      return `<div class="company-group">
        <div class="company-header">
          <h3>${escapeHtml(company)}</h3>
          <span class="tag muted">${jobs.length} open role${jobs.length > 1 ? "s" : ""}</span>
          ${meta.company_size ? `<span class="tag muted">${escapeHtml(meta.company_size)}</span>` : ""}
          ${meta.industry ? `<span class="tag muted">${escapeHtml(meta.industry)}</span>` : ""}
          ${connBadge}
        </div>
        <div class="company-jobs">${shown.map(j => jobCardHtml(j, true)).join("")}</div>
        ${remaining > 0 ? `<div class="load-more"><button class="btn show-more-company" data-company="${escapeHtml(company)}">Show ${remaining} more at ${escapeHtml(company)}</button></div>` : ""}
      </div>`;
    }).join("");
    loadMoreWrap.style.display = "none";
    listEl.querySelectorAll(".show-more-company").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        expandedCompanies.add(btn.dataset.company);
        render();
      });
    });
  } else {
    const page = filtered.slice(0, visibleCount);
    listEl.innerHTML = page.map(j => jobCardHtml(j, true)).join("");
    loadMoreWrap.style.display = filtered.length > visibleCount ? "block" : "none";
  }

  listEl.querySelectorAll(".icon-btn[data-action]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.action === "like") {
        likedIds.has(id) ? likedIds.delete(id) : likedIds.add(id);
        saveIdSet("jobfit_liked", likedIds);
      } else if (btn.dataset.action === "sent") {
        sentIds.has(id) ? sentIds.delete(id) : sentIds.add(id);
        saveIdSet("jobfit_sent", sentIds);
      } else if (btn.dataset.action === "reached") {
        reachedIds.has(id) ? reachedIds.delete(id) : reachedIds.add(id);
        saveIdSet("jobfit_reached", reachedIds);
      } else if (btn.dataset.action === "hide") {
        hiddenIds.has(id) ? hiddenIds.delete(id) : hiddenIds.add(id);
        saveIdSet("jobfit_hidden", hiddenIds);
      } else if (btn.dataset.action === "hide-company") {
        const company = btn.dataset.company;
        hiddenCompanies.has(company) ? hiddenCompanies.delete(company) : hiddenCompanies.add(company);
        saveIdSet("jobfit_hidden_companies", hiddenCompanies);
      }
      updateStatBlock();
      render();
    });
  });
}

function renderChecklist(elId, values, selectedSet, onChange, withCounts, countField) {
  const el = document.getElementById(elId);
  const counts = {};
  if (withCounts) {
    for (const j of JOBS) {
      const v = j[countField];
      if (v) counts[v] = (counts[v] || 0) + 1;
    }
  }
  el.innerHTML = values.map(v => `
    <label class="check-item" title="${escapeHtml(v)}">
      <input type="checkbox" data-val="${escapeHtml(v)}" ${selectedSet.has(v) || selectedSet === null ? "checked" : ""}>
      <span class="label-text">${escapeHtml(v)}</span>
      <button type="button" class="only-btn" data-val="${escapeHtml(v)}">only</button>
      ${withCounts ? `<span class="count">${counts[v] || 0}</span>` : ""}
    </label>`).join("");
  el.querySelectorAll("input").forEach(inp => {
    inp.addEventListener("change", () => {
      const val = inp.dataset.val;
      onChange(val, inp.checked);
      render();
    });
  });
  el.querySelectorAll(".only-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const val = btn.dataset.val;
      selectedSet.clear();
      selectedSet.add(val);
      onChange(val, true);
      renderChecklist(elId, values, selectedSet, onChange, withCounts, countField);
      render();
    });
  });
}

function setupCompanyList(filterText) {
  const values = filterText
    ? allCompanies.filter(c => c.toLowerCase().includes(filterText.toLowerCase()))
    : allCompanies;
  renderChecklist("companyList", values, state.companies, (val, checked) => {
    if (checked) state.companies.add(val); else state.companies.delete(val);
    document.getElementById("companySelCount").textContent = state.companies.size < allCompanies.length ? `(${state.companies.size})` : "";
  }, true, "company");
}
function refreshCityList() {
  renderChecklist("cityList", allCities, state.cities, (val, checked) => { if (checked) state.cities.add(val); else state.cities.delete(val); }, true, "city");
}
function refreshIndustryList() {
  renderChecklist("industryList", allIndustries, state.industries, (val, checked) => { if (checked) state.industries.add(val); else state.industries.delete(val); }, true, "industry");
}
function refreshDeptList() {
  renderChecklist("deptList", allDepts, state.depts, (val, checked) => { if (checked) state.depts.add(val); else state.depts.delete(val); }, true, "department");
}
function refreshYearsList() {
  renderChecklist("yearsList", YEARS_BUCKETS, state.years, (val, checked) => { if (checked) state.years.add(val); else state.years.delete(val); }, true, "_yearsBucket");
}
function refreshLanguageList() {
  renderChecklist("languageList", allLanguages, state.languages, (val, checked) => { if (checked) state.languages.add(val); else state.languages.delete(val); }, false);
}

document.querySelectorAll(".bulk-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.bulk;
    const action = btn.dataset.action;
    if (target === "company") {
      state.companies = action === "all" ? new Set(allCompanies) : new Set();
      document.getElementById("companySelCount").textContent = state.companies.size < allCompanies.length ? `(${state.companies.size})` : "";
      setupCompanyList(document.getElementById("companySearch").value);
    } else if (target === "city") {
      state.cities = new Set();
      refreshCityList();
    } else if (target === "industry") {
      state.industries = new Set();
      refreshIndustryList();
    } else if (target === "dept") {
      state.depts = new Set();
      refreshDeptList();
    } else if (target === "years") {
      state.years = new Set();
      refreshYearsList();
    } else if (target === "language") {
      state.languages = new Set();
      refreshLanguageList();
    }
    render();
  });
});

document.getElementById("qSearch").addEventListener("input", (e) => { state.q = e.target.value; visibleCount = PAGE_SIZE; render(); });
document.getElementById("qExcludeTitle").addEventListener("input", (e) => { state.excludeTitle = e.target.value; refreshExcludeSuggestions(); visibleCount = PAGE_SIZE; render(); });
document.getElementById("qModeBtn").addEventListener("click", () => {
  state.qMode = state.qMode === "AND" ? "OR" : "AND";
  syncModeBtn("qModeBtn", state.qMode);
  render();
});
document.getElementById("qScopeBtn").addEventListener("click", () => {
  state.qScope = QSCOPE_CYCLE[state.qScope];
  syncScopeBtn();
  render();
});
document.getElementById("excludeModeBtn").addEventListener("click", () => {
  state.excludeMode = state.excludeMode === "AND" ? "OR" : "AND";
  syncModeBtn("excludeModeBtn", state.excludeMode);
  render();
});
document.getElementById("cvSelect").addEventListener("change", (e) => { state.cv = e.target.value; render(); });
document.getElementById("minScore").addEventListener("input", (e) => {
  state.minScore = Number(e.target.value);
  document.getElementById("minScoreVal").textContent = state.minScore;
  render();
});
document.getElementById("fRemote").addEventListener("change", (e) => { state.remote = e.target.checked; render(); });
document.getElementById("fConn").addEventListener("change", (e) => { state.conn = e.target.checked; render(); });
document.getElementById("fDesc").addEventListener("change", (e) => { state.desc = e.target.checked; render(); });
document.getElementById("fLiked").addEventListener("change", (e) => { state.likedOnly = e.target.checked; render(); });
document.getElementById("fShowHidden").addEventListener("change", (e) => { state.showHidden = e.target.checked; render(); });
document.getElementById("fShowSent").addEventListener("change", (e) => { state.showSent = e.target.checked; render(); });
document.getElementById("fShowReached").addEventListener("change", (e) => { state.showReached = e.target.checked; render(); });
document.getElementById("qtLiked").addEventListener("click", () => {
  state.likedOnly = !state.likedOnly;
  document.getElementById("fLiked").checked = state.likedOnly;
  render();
});
document.getElementById("qtHidden").addEventListener("click", () => {
  state.showHidden = !state.showHidden;
  document.getElementById("fShowHidden").checked = state.showHidden;
  render();
});
document.getElementById("qtSent").addEventListener("click", () => {
  state.showSent = !state.showSent;
  document.getElementById("fShowSent").checked = state.showSent;
  render();
});
document.getElementById("qtReached").addEventListener("click", () => {
  state.showReached = !state.showReached;
  document.getElementById("fShowReached").checked = state.showReached;
  render();
});
document.getElementById("fShowClosed").addEventListener("change", (e) => { state.showClosed = e.target.checked; render(); });
document.getElementById("fReferral").addEventListener("change", (e) => { state.referralOnly = e.target.checked; render(); });
document.getElementById("groupToggle").addEventListener("change", (e) => { state.group = e.target.checked; render(); });
document.getElementById("sortSelect").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
document.getElementById("companySearch").addEventListener("input", (e) => setupCompanyList(e.target.value));
document.getElementById("loadMoreBtn").addEventListener("click", () => { visibleCount += PAGE_SIZE; render(); });
document.getElementById("resetBtn").addEventListener("click", () => {
  state.q = ""; state.qMode = "OR"; state.qScope = "both"; state.excludeTitle = ""; state.excludeMode = "OR"; state.cv = "best"; state.minScore = 0; state.remote = false; state.conn = false; state.desc = false;
  state.companies = new Set(allCompanies); state.cities = new Set(); state.industries = new Set(); state.depts = new Set();
  state.years = new Set(); state.languages = new Set();
  state.likedOnly = false; state.showHidden = false; state.showSent = false; state.showReached = false; state.referralOnly = false; state.showClosed = false;
  document.getElementById("qSearch").value = ""; document.getElementById("qExcludeTitle").value = ""; document.getElementById("cvSelect").value = "best";
  syncModeBtn("qModeBtn", "OR"); syncModeBtn("excludeModeBtn", "OR"); syncScopeBtn();
  document.getElementById("minScore").value = 0; document.getElementById("minScoreVal").textContent = "0";
  document.getElementById("fRemote").checked = false; document.getElementById("fConn").checked = false; document.getElementById("fDesc").checked = false;
  document.getElementById("fLiked").checked = false; document.getElementById("fShowHidden").checked = false; document.getElementById("fShowSent").checked = false; document.getElementById("fShowReached").checked = false; document.getElementById("fReferral").checked = false;
  document.getElementById("fShowClosed").checked = false;
  document.getElementById("companySearch").value = "";
  setupCompanyList("");
  refreshCityList();
  refreshIndustryList();
  refreshDeptList();
  refreshYearsList();
  refreshLanguageList();
  refreshExcludeSuggestions();
  visibleCount = PAGE_SIZE;
  render();
});
document.getElementById("clearDataBtn").addEventListener("click", () => {
  try { localStorage.clear(); } catch (e) { /* private mode / storage blocked */ }
  location.reload();
});

setupCompanyList("");
refreshCityList();
refreshIndustryList();
refreshDeptList();
refreshYearsList();
refreshLanguageList();
refreshSavedFiltersList();
refreshExcludeSuggestions();

function saveCurrentFilterAs() {
  const nameInput = document.getElementById("saveFilterName");
  const name = nameInput.value.trim();
  if (!name) return;
  const all = loadSavedFilters();
  all[name] = captureFilterSnapshot();
  persistSavedFilters(all);
  nameInput.value = "";
  refreshSavedFiltersList();
}
document.getElementById("saveFilterBtn").addEventListener("click", saveCurrentFilterAs);
document.getElementById("saveFilterName").addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveCurrentFilterAs();
});

const companiesCount = allCompanies.length;
const connCount = JOBS.filter(j => j.has_connection).length;
const descCount = JOBS.filter(j => j.has_description).length;
const referralCount = JOBS.filter(j => j.is_referral).length;
function updateStatBlock() {
  document.getElementById("statBlock").innerHTML =
    `${JOBS.length} jobs &middot; ${companiesCount} companies<br>${connCount} with a connection<br>${descCount} with full description<br>${referralCount} referral jobs<br>${likedIds.size} liked &middot; ${hiddenIds.size} hidden &middot; ${sentIds.size} CV sent &middot; ${reachedIds.size} reached out<br>generated ${GENERATED_AT}`;
}
updateStatBlock();

render();
</script>
</body>
</html>
"""


def render(dataset: list[dict], profiles: list[dict]) -> str:
    jobs_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    profiles_json = json.dumps(profiles, ensure_ascii=False)
    generated_at = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return (
        PAGE_TEMPLATE.replace("__JOBS_JSON__", jobs_json)
        .replace("__PROFILES_JSON__", profiles_json)
        .replace("__GENERATED_AT_JSON__", generated_at)
    )


def build(dataset: list[dict] | None = None) -> None:
    if dataset is None:
        dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    from jobfit import cv
    profiles = [{"id": pid, "name": entry["name"]} for pid, entry in cv.load_registry().items()]
    html = render(dataset, profiles)
    config.OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {config.OUTPUT_HTML} ({len(dataset)} jobs)")


if __name__ == "__main__":
    build()
