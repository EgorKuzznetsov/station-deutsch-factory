/**
 * Station Deutsch — панель управления контент-фабрикой (Cloudflare Worker, бесплатный тариф).
 *
 * Что делает:
 *  • мобильная панель: статус, кнопка «создать сейчас», история, статистика, настройки дизайна и расписания;
 *  • хранит настройки, историю и OAuth-токены в Cloudflare KV (токены не попадают в GitHub);
 *  • раз в 5 минут (Cron Trigger) проверяет расписание и запускает GitHub Actions;
 *  • OAuth-вход в YouTube и TikTok прямо с iPhone, автоматическое обновление токенов;
 *  • API для фабрики (GitHub Actions) с секретным ключом;
 *  • страницы /privacy и /terms для регистрации приложений Google и TikTok.
 *
 * Привязки (Settings → Variables and Secrets / Bindings):
 *   KV                     — KV namespace
 *   ADMIN_PASSWORD         — пароль входа в панель (secret)
 *   FACTORY_SECRET         — общий секрет с GitHub (secret)
 *   GH_TOKEN               — fine-grained GitHub token: Actions RW, Contents RW (secret)
 *   GH_REPO                — "логин/репозиторий" (text)
 *   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET     — OAuth-клиент Google (secret)
 *   TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET    — приложение TikTok for Developers (secret)
 * Cron Trigger: every 5 minutes
 */

const DEFAULTS = {
  enabled: true, botHandle: "station_deutsch_bot", botUrl: "https://t.me/station_deutsch_bot",
  uiLang: "uk", timezone: "Europe/Berlin", schedule: ["12:00", "18:00"],
  levels: ["A1", "A2", "B1", "B2"], theme: "rotate", thinkSeconds: 5, sound: { enabled: true, volume: 70 }, cooldownDays: 60, autoOptimize: true,
  cta: { line1: "", line3: "" },
  formats: Object.fromEntries(["translate", "find_error", "article", "level_test", "a1_vs_b2", "five_words",
    "phrases", "fill_blank", "mistakes", "mini_exam"].map(f => [f, { enabled: true, weight: f === "article" ? 1.2 : 1 }])),
  publish: { youtube: true, tiktok: true, telegram: true },
  youtube: { privacy: "public" },
  tiktok: { mode: "inbox", privacy: "PUBLIC_TO_EVERYONE", requireApproval: false, disableComment: false,
    disableDuet: false, disableStitch: false, brandOrganic: true },
};

const YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly";
const TT_SCOPES = "user.info.basic,video.upload,video.publish,video.list";

// ------------------------------------------------------------------ утилиты
const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });
const html = (body, status = 200, headers = {}) =>
  new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8",
    "content-security-policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src * data:", ...headers } });
const redirect = (to, headers = {}) => new Response(null, { status: 302, headers: { location: to, ...headers } });

function merge(base, over) {
  const out = structuredClone(base);
  for (const [k, v] of Object.entries(over || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && typeof out[k] === "object" && !Array.isArray(out[k])) out[k] = merge(out[k] || {}, v);
    else out[k] = v;
  }
  return out;
}
async function kvGet(env, key, def) {
  const v = await env.KV.get(key, "json");
  return v === null ? def : v;
}
const kvPut = (env, key, val, opts) => env.KV.put(key, JSON.stringify(val), opts);

async function hmac(key, msg) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}
function safeEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
const sessKey = env => (env.ADMIN_PASSWORD || "") + "|" + (env.FACTORY_SECRET || "");
async function makeSession(env) {
  const exp = Date.now() + 30 * 864e5;
  return exp + "." + (await hmac(sessKey(env), "s" + exp));
}
async function isAuthed(req, env) {
  const c = req.headers.get("cookie") || "";
  const m = c.match(/sd_session=([^;]+)/);
  if (!m) return false;
  const [exp, sig] = m[1].split(".");
  if (!exp || Number(exp) < Date.now()) return false;
  return safeEq(sig, await hmac(sessKey(env), "s" + exp));
}
function isFactory(req, env) {
  const a = req.headers.get("authorization") || "";
  return !!env.FACTORY_SECRET && safeEq(a, "Bearer " + env.FACTORY_SECRET);
}
const randomId = () => [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, "0")).join("");

function localNow(tz) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: tz || "Europe/Berlin",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
    .formatToParts(new Date()).map(p => [p.type, p.value]));
  const hh = parts.hour === "24" ? "00" : parts.hour;
  return { date: `${parts.year}-${parts.month}-${parts.day}`, hm: `${hh}:${parts.minute}`, min: Number(hh) * 60 + Number(parts.minute) };
}
const toMin = hm => { const [h, m] = String(hm).split(":").map(Number); return h * 60 + m; };

// ------------------------------------------------------------------ данные
const getSettings = async env => merge(DEFAULTS, await kvGet(env, "settings", {}));
const getHistory = env => kvGet(env, "history", []);
async function upsertHistory(env, entries) {
  let h = await getHistory(env);
  const map = new Map(h.map(e => [e.id, e]));
  for (const e of entries) map.set(e.id, { ...(map.get(e.id) || {}), ...e });
  h = [...map.values()].sort((a, b) => (a.created || "").localeCompare(b.created || "")).slice(-400);
  await kvPut(env, "history", h);
  return h;
}

// ------------------------------------------------------------------ GitHub
async function gh(env, path, method = "GET", body) {
  if (!env.GH_TOKEN || !env.GH_REPO) throw new Error("GH_TOKEN / GH_REPO не настроены");
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}${path}`, {
    method, body: body ? JSON.stringify(body) : undefined,
    headers: { authorization: "Bearer " + env.GH_TOKEN, accept: "application/vnd.github+json",
      "user-agent": "station-deutsch-panel", "x-github-api-version": "2022-11-28" },
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.status === 204 ? {} : r.json();
}
const dispatch = (env, wf, inputs = {}) =>
  gh(env, `/actions/workflows/${wf}/dispatches`, "POST", { ref: env.GH_BRANCH || "main", inputs });

// ------------------------------------------------------------------ токены
async function youtubeToken(env) {
  const t = await kvGet(env, "yt_tokens", null);
  if (!t?.refresh_token) throw new Error("YouTube не подключён");
  if (t.access_token && t.expires_at > Date.now() + 120e3) return t.access_token;
  const r = await fetch("https://oauth2.googleapis.com/token", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET,
      refresh_token: t.refresh_token, grant_type: "refresh_token" }) });
  const d = await r.json();
  if (!d.access_token) throw new Error("Google: " + JSON.stringify(d).slice(0, 200));
  await kvPut(env, "yt_tokens", { ...t, access_token: d.access_token, expires_at: Date.now() + d.expires_in * 1000 });
  return d.access_token;
}
async function tiktokToken(env) {
  const t = await kvGet(env, "tt_tokens", null);
  if (!t?.refresh_token) throw new Error("TikTok не подключён");
  if (t.access_token && t.expires_at > Date.now() + 300e3) return t.access_token;
  const r = await fetch("https://open.tiktokapis.com/v2/oauth/token/", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_key: env.TIKTOK_CLIENT_KEY, client_secret: env.TIKTOK_CLIENT_SECRET,
      grant_type: "refresh_token", refresh_token: t.refresh_token }) });
  const d = await r.json();
  if (!d.access_token) throw new Error("TikTok: " + JSON.stringify(d).slice(0, 200));
  await kvPut(env, "tt_tokens", { ...t, access_token: d.access_token, refresh_token: d.refresh_token || t.refresh_token,
    expires_at: Date.now() + d.expires_in * 1000, refresh_expires_at: Date.now() + (d.refresh_expires_in || 31536000) * 1000 });
  return d.access_token;
}
async function tiktokCreator(env) {
  const tok = await tiktokToken(env);
  const r = await fetch("https://open.tiktokapis.com/v2/post/publish/creator_info/query/", { method: "POST",
    headers: { authorization: "Bearer " + tok, "content-type": "application/json; charset=UTF-8" }, body: "{}" });
  return r.json();
}

// ------------------------------------------------------------------ OAuth
async function oauthStart(env, origin, platform) {
  const state = randomId();
  await env.KV.put("oauth_state:" + state, platform, { expirationTtl: 900 });
  const cb = `${origin}/oauth/${platform}/callback`;
  if (platform === "youtube") {
    if (!env.GOOGLE_CLIENT_ID) return html(page("Ошибка", "<p>Не задан GOOGLE_CLIENT_ID</p>"), 400);
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID, redirect_uri: cb, response_type: "code", scope: YT_SCOPES,
      access_type: "offline", prompt: "consent", include_granted_scopes: "true", state }));
  }
  if (!env.TIKTOK_CLIENT_KEY) return html(page("Ошибка", "<p>Не задан TIKTOK_CLIENT_KEY</p>"), 400);
  return redirect("https://www.tiktok.com/v2/auth/authorize/?" + new URLSearchParams({
    client_key: env.TIKTOK_CLIENT_KEY, scope: TT_SCOPES, response_type: "code", redirect_uri: cb, state }));
}
async function oauthCallback(env, url, platform) {
  const state = url.searchParams.get("state"), code = url.searchParams.get("code");
  const ok = state && (await env.KV.get("oauth_state:" + state)) === platform;
  if (!ok || !code) return html(page("Ошибка входа", `<p>Неверный state или нет кода. ${escapeHtml(url.searchParams.get("error_description") || url.searchParams.get("error") || "")}</p><p><a href="/">← В панель</a></p>`), 400);
  await env.KV.delete("oauth_state:" + state);
  const cb = `${url.origin}/oauth/${platform}/callback`;
  if (platform === "youtube") {
    const r = await fetch("https://oauth2.googleapis.com/token", { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ code, client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: cb, grant_type: "authorization_code" }) });
    const d = await r.json();
    if (!d.refresh_token) return html(page("Ошибка", `<p>Google не вернул refresh token: ${escapeHtml(JSON.stringify(d).slice(0, 300))}</p><p>Удали доступ приложения в аккаунте Google и попробуй снова.</p>`), 400);
    let channel = "";
    try {
      const c = await (await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        { headers: { authorization: "Bearer " + d.access_token } })).json();
      channel = c.items?.[0]?.snippet?.title || "";
    } catch (e) { /* не критично */ }
    await kvPut(env, "yt_tokens", { refresh_token: d.refresh_token, access_token: d.access_token,
      expires_at: Date.now() + d.expires_in * 1000, channel, connected_at: new Date().toISOString() });
    return redirect("/?ok=youtube");
  }
  const r = await fetch("https://open.tiktokapis.com/v2/oauth/token/", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_key: env.TIKTOK_CLIENT_KEY, client_secret: env.TIKTOK_CLIENT_SECRET, code,
      grant_type: "authorization_code", redirect_uri: cb }) });
  const d = await r.json();
  if (!d.access_token) return html(page("Ошибка", `<p>TikTok: ${escapeHtml(JSON.stringify(d).slice(0, 300))}</p>`), 400);
  let nickname = "";
  try {
    const u = await (await fetch("https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name",
      { headers: { authorization: "Bearer " + d.access_token } })).json();
    nickname = u.data?.user?.display_name || "";
  } catch (e) { /* не критично */ }
  await kvPut(env, "tt_tokens", { access_token: d.access_token, refresh_token: d.refresh_token, open_id: d.open_id,
    scope: d.scope, expires_at: Date.now() + d.expires_in * 1000,
    refresh_expires_at: Date.now() + (d.refresh_expires_in || 31536000) * 1000, nickname, connected_at: new Date().toISOString() });
  return redirect("/?ok=tiktok");
}

// ------------------------------------------------------------------ расписание
async function tick(env) {
  const s = await getSettings(env);
  const now = localNow(s.timezone);
  if (s.enabled) {
    for (const slot of s.schedule || []) {
      const d = now.min - toMin(slot);
      if (d >= 0 && d < 30) {
        const key = `run:${now.date}:${slot}`;
        if (!(await env.KV.get(key))) {
          await env.KV.put(key, "1", { expirationTtl: 172800 });
          try { await dispatch(env, "produce.yml", { count: "1", slot }); }
          catch (e) { await env.KV.put("last_error", String(e).slice(0, 500)); }
        }
      }
    }
  }
}

function nextRuns(s) {
  const now = localNow(s.timezone);
  const slots = [...(s.schedule || [])].sort((a, b) => toMin(a) - toMin(b));
  return slots.map(x => ({ time: x, today: toMin(x) > now.min }));
}

// ------------------------------------------------------------------ API панели
async function apiState(env) {
  const [settings, history, stats, yt, tt, lastError] = await Promise.all([
    getSettings(env), getHistory(env), kvGet(env, "stats", {}), kvGet(env, "yt_tokens", null),
    kvGet(env, "tt_tokens", null), env.KV.get("last_error")]);
  let runs = [];
  try {
    const r = await gh(env, "/actions/runs?per_page=6");
    runs = (r.workflow_runs || []).map(x => ({ name: x.name, status: x.status, conclusion: x.conclusion,
      created: x.created_at, url: x.html_url }));
  } catch (e) { runs = [{ name: "GitHub", status: "error", conclusion: String(e).slice(0, 120) }]; }
  const today = localNow(settings.timezone).date;
  return {
    settings, stats, runs, lastError, repo: env.GH_REPO || "",
    history: history.slice(-150).reverse(),
    today: history.filter(h => (h.created || "").slice(0, 10) === today).length,
    next: nextRuns(settings),
    connections: {
      youtube: yt ? { connected: true, channel: yt.channel, since: yt.connected_at } : { connected: false, configured: !!env.GOOGLE_CLIENT_ID },
      tiktok: tt ? { connected: true, nickname: tt.nickname, scope: tt.scope, refreshUntil: tt.refresh_expires_at }
        : { connected: false, configured: !!env.TIKTOK_CLIENT_KEY },
      github: { configured: !!(env.GH_TOKEN && env.GH_REPO) },
    },
  };
}

function cleanSettings(input) {
  const s = merge(DEFAULTS, input || {});
  s.schedule = (s.schedule || []).filter(x => /^\d{2}:\d{2}$/.test(x)).slice(0, 6);
  s.thinkSeconds = Math.min(8, Math.max(3, Number(s.thinkSeconds) || 5));
  s.sound.volume = Math.min(100, Math.max(0, Number(s.sound.volume) || 0));
  s.sound.enabled = !!s.sound.enabled;
  s.cooldownDays = Math.min(365, Math.max(7, Number(s.cooldownDays) || 60));
  s.levels = (s.levels || []).filter(l => ["A1", "A2", "B1", "B2"].includes(l));
  if (!s.levels.length) s.levels = ["A1", "A2", "B1", "B2"];
  s.botHandle = String(s.botHandle || "").replace(/^@/, "").slice(0, 64);
  for (const f of Object.keys(s.formats)) s.formats[f].weight = Math.min(5, Math.max(0, Number(s.formats[f].weight) || 0));
  return s;
}

// ------------------------------------------------------------------ роутер
export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(tick(env)); },

  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      // --- публичные страницы
      if (p === "/privacy") return html(page("Privacy Policy", PRIVACY));
      if (p === "/terms") return html(page("Terms of Service", TERMS));
      if (p === "/health") return json({ ok: true });

      // --- API фабрики (GitHub Actions)
      if (p.startsWith("/factory/")) {
        if (!isFactory(req, env)) return json({ error: "unauthorized" }, 401);
        if (p === "/factory/config") return json({ settings: await getSettings(env), weights: (await kvGet(env, "stats", {})).weights || {} });
        if (p === "/factory/token/youtube") return json({ access_token: await youtubeToken(env) });
        if (p === "/factory/token/tiktok") return json({ access_token: await tiktokToken(env) });
        if (p === "/factory/history" && req.method === "POST") { await upsertHistory(env, [await req.json()]); return json({ ok: true }); }
        if (p === "/factory/history/bulk" && req.method === "POST") { await upsertHistory(env, (await req.json()).items || []); return json({ ok: true }); }
        if (p === "/factory/stats" && req.method === "POST") { await kvPut(env, "stats", await req.json()); return json({ ok: true }); }
        return json({ error: "not found" }, 404);
      }

      // --- вход
      if (p === "/login" && req.method === "POST") {
        const f = await req.formData();
        if (env.ADMIN_PASSWORD && safeEq(String(f.get("password") || ""), env.ADMIN_PASSWORD)) {
          return redirect("/", { "set-cookie": `sd_session=${await makeSession(env)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000` });
        }
        await new Promise(r => setTimeout(r, 1200));
        return html(LOGIN.replace("<!--ERR-->", '<p class="err">Неверный пароль</p>'), 401);
      }
      if (p === "/logout") return redirect("/", { "set-cookie": "sd_session=; Path=/; Max-Age=0" });
      if (p.match(/^\/oauth\/(youtube|tiktok)\/callback$/)) return oauthCallback(env, url, p.split("/")[2]);

      const authed = await isAuthed(req, env);
      if (!authed) return p.startsWith("/api/") ? json({ error: "unauthorized" }, 401) : html(LOGIN);

      if (p === "/" || p === "/index.html") return html(APP);
      if (p.match(/^\/oauth\/(youtube|tiktok)\/start$/)) return oauthStart(env, url.origin, p.split("/")[2]);
      if (p === "/api/state") return json(await apiState(env));
      if (p === "/api/settings" && req.method === "POST") {
        const s = cleanSettings(await req.json());
        await kvPut(env, "settings", s);
        return json({ ok: true, settings: s });
      }
      if (p === "/api/run" && req.method === "POST") {
        const b = await req.json().catch(() => ({}));
        await dispatch(env, "produce.yml", { count: String(Math.min(3, Math.max(1, Number(b.count) || 1))), format: b.format || "", slot: "manual" });
        return json({ ok: true });
      }
      if (p === "/api/analytics" && req.method === "POST") { await dispatch(env, "analytics.yml"); return json({ ok: true }); }
      if (p === "/api/approve" && req.method === "POST") {
        const { id, platform = "tiktok" } = await req.json();
        const h = await getHistory(env);
        const e = h.find(x => x.id === id);
        if (!e?.runId) return json({ error: "Не найден запуск с видео (runId)" }, 400);
        await dispatch(env, "publish.yml", { id, run_id: String(e.runId), platform });
        await upsertHistory(env, [{ id, [platform]: { ...(e[platform] || {}), status: "publishing" } }]);
        return json({ ok: true });
      }
      if (p === "/api/tiktok/creator") return json(await tiktokCreator(env));
      if (p === "/api/disconnect" && req.method === "POST") {
        const { platform } = await req.json();
        await env.KV.delete(platform === "youtube" ? "yt_tokens" : "tt_tokens");
        return json({ ok: true });
      }
      if (p === "/api/clear-error" && req.method === "POST") { await env.KV.delete("last_error"); return json({ ok: true }); }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: String(e.message || e).slice(0, 500) }, 500);
    }
  },
};

// ------------------------------------------------------------------ страницы
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function page(title, body) {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>
<style>body{font:16px/1.55 -apple-system,system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px;color:#14112b}h1{font-size:24px}a{color:#5b3df5}</style></head><body><h1>${title}</h1>${body}</body></html>`;
}
const PRIVACY = `<p>Station Deutsch Content Factory is a private tool operated by the owner of the Telegram bot @station_deutsch_bot. It is used only to publish the owner's own educational videos to the owner's own YouTube and TikTok accounts.</p>
<p><b>Data we access:</b> OAuth tokens and basic profile information (channel name / display name) of the owner's accounts, and public statistics (views, likes, comments, shares) of videos published by this tool.</p>
<p><b>Storage:</b> tokens are stored encrypted at rest in Cloudflare KV and are never shared with third parties. No data of other users is collected.</p>
<p><b>Deletion:</b> access can be revoked at any time in the Google account / TikTok settings or in the dashboard ("Disconnect"), which deletes stored tokens immediately.</p>
<p>Contact: via Telegram @station_deutsch_bot.</p>`;
const TERMS = `<p>This tool is provided for personal use by its owner to publish original, programmatically generated German-language learning videos. Users must comply with the YouTube Terms of Service, YouTube API Services Terms, Google Privacy Policy, and TikTok Terms of Service and Community Guidelines. The tool is provided "as is" without warranties.</p>`;

const CSS = `
:root{--bg:#0d0b1f;--card:#191633;--card2:#221e44;--tx:#f3f1ff;--mu:#9b96c8;--ac:#ffd23f;--ok:#2bd97c;--bad:#ff5a6e;--line:#2e2a55}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;padding:env(safe-area-inset-top) 0 calc(84px + env(safe-area-inset-bottom))}
header{padding:18px 18px 6px;display:flex;align-items:center;justify-content:space-between}
h1{font-size:22px;margin:0;font-weight:800;letter-spacing:-.3px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mu);margin:22px 18px 8px;font-weight:700}
.card{background:var(--card);border-radius:18px;margin:0 14px 12px;padding:16px;border:1px solid var(--line)}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:34px}
.row+.row{border-top:1px solid var(--line);margin-top:8px;padding-top:8px}
.mu{color:var(--mu);font-size:13px}.big{font-size:30px;font-weight:800}
.btn{appearance:none;border:0;border-radius:14px;padding:14px 16px;font-weight:700;font-size:16px;background:var(--ac);color:#14112b;width:100%}
.btn.sec{background:var(--card2);color:var(--tx)}.btn.sm{width:auto;padding:9px 14px;font-size:14px;border-radius:11px}
.btn:active{transform:scale(.98)}.btn[disabled]{opacity:.5}
.chip{display:inline-block;padding:3px 9px;border-radius:99px;font-size:12px;font-weight:700;background:var(--card2);color:var(--mu)}
.chip.ok{background:rgba(43,217,124,.15);color:var(--ok)}.chip.bad{background:rgba(255,90,110,.15);color:var(--bad)}.chip.ac{background:rgba(255,210,63,.15);color:var(--ac)}
input[type=text],input[type=time],input[type=password],select{width:100%;background:var(--card2);border:1px solid var(--line);color:var(--tx);border-radius:12px;padding:11px 12px;font-size:16px}
input[type=range]{width:120px;accent-color:var(--ac)}
.sw{position:relative;width:50px;height:30px;flex:none}.sw input{display:none}.sw span{position:absolute;inset:0;background:var(--card2);border-radius:99px;transition:.2s}
.sw span:after{content:"";position:absolute;width:24px;height:24px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
.sw input:checked+span{background:var(--ok)}.sw input:checked+span:after{left:23px}
nav{position:fixed;bottom:0;left:0;right:0;background:rgba(13,11,31,.92);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid var(--line);display:flex;padding:6px 6px calc(6px + env(safe-area-inset-bottom))}
nav button{flex:1;background:none;border:0;color:var(--mu);font-size:11px;font-weight:600;padding:6px 0}
nav button b{display:block;font-size:20px;line-height:1.2}nav button.on{color:var(--ac)}
.themes{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.th{border-radius:14px;padding:10px;border:2px solid transparent;text-align:center;font-size:12px;font-weight:700}
.th.on{border-color:var(--ac)}.th i{display:block;height:46px;border-radius:10px;margin-bottom:6px}
.hist .t{font-weight:700;margin:4px 0 6px}.bar{height:8px;border-radius:9px;background:var(--card2);overflow:hidden}.bar i{display:block;height:100%;background:var(--ac)}
.toast{position:fixed;left:50%;bottom:96px;transform:translateX(-50%);background:#fff;color:#14112b;padding:10px 16px;border-radius:12px;font-weight:700;opacity:0;transition:.3s;pointer-events:none;max-width:90%}
.toast.on{opacity:1}.save{position:sticky;bottom:calc(84px + env(safe-area-inset-bottom));padding:0 14px 10px}
a{color:var(--ac)}.err{color:var(--bad)}small{color:var(--mu)}
`;

const LOGIN = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0d0b1f"><title>Station Deutsch · Вход</title><style>${CSS}</style></head>
<body><header><h1>🇩🇪 Контент-фабрика</h1></header><form class="card" method="post" action="/login" style="margin-top:30px">
<p class="mu">Station Deutsch · TikTok + YouTube Shorts</p><!--ERR-->
<input type="password" name="password" placeholder="Пароль" autocomplete="current-password" required>
<div style="height:12px"></div><button class="btn">Войти</button></form></body></html>`;

const APP = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="theme-color" content="#0d0b1f">
<title>Station Deutsch · Фабрика</title><style>${CSS}</style></head><body>
<header><h1 id="ttl">Главная</h1><button class="btn sec sm" onclick="load()">↻</button></header>
<main id="v"></main><div class="toast" id="toast"></div>
<nav><button data-t="home" class="on"><b>⌂</b>Главная</button><button data-t="hist"><b>▤</b>История</button><button data-t="stats"><b>▲</b>Статистика</button><button data-t="set"><b>⚙</b>Настройки</button></nav>
<script>
var S=null, TAB='home', DRAFT=null;
var FMT={translate:'Переклад / перевод',find_error:'Найди ошибку',article:'der · die · das',level_test:'Тест уровня',a1_vs_b2:'A1 против B2',five_words:'5 слов',phrases:'Разговорные фразы',fill_blank:'Вставь слово',mistakes:'Типичные ошибки',mini_exam:'Мини-экзамен'};
var THEMES={rotate:['Чередовать','linear-gradient(135deg,#5B3DF5,#FF6A3D,#22E3B5)'],midnight:['Midnight','linear-gradient(160deg,#0B1026,#2A1659 60%,#FFD23F 61%)'],sunset:['Sunset','linear-gradient(160deg,#2B0F3A,#FF6A3D 60%,#FFF15A 61%)'],mint:['Mint','linear-gradient(160deg,#03241F,#0E6B5C 60%,#B6FF3D 61%)'],berlin:['Berlin','linear-gradient(160deg,#0A0A0A,#262626 60%,#FFCE00 61%)'],paper:['Paper','linear-gradient(160deg,#FFF7E8,#FFE3C2 60%,#1B1B2F 61%)']};
function $(s){return document.querySelector(s)}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function toast(m){var t=$('#toast');t.textContent=m;t.classList.add('on');setTimeout(function(){t.classList.remove('on')},2600)}
function api(path,body){return fetch(path,{method:body?'POST':'GET',headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined}).then(function(r){return r.json().then(function(d){if(!r.ok)throw new Error(d.error||r.status);return d})})}
function load(){api('/api/state').then(function(d){S=d;DRAFT=JSON.parse(JSON.stringify(d.settings));render()}).catch(function(e){if(String(e).indexOf('unauthorized')>=0)location.reload();else toast('Ошибка: '+e.message)})}
function sw(id,on){return '<label class="sw"><input type="checkbox" id="'+id+'"'+(on?' checked':'')+'><span></span></label>'}
function chip(st){var ok=['published','sent_to_inbox','ok'],bad=['error','failed','locked_private'];var c=ok.indexOf(st)>=0?'ok':bad.indexOf(st)>=0?'bad':'ac';var L={published:'опубликовано',sent_to_inbox:'во входящих TikTok',awaiting_approval:'ждёт одобрения',publishing:'публикуется',processing:'обрабатывается',error:'ошибка',failed:'ошибка',locked_private:'приватно (нужен аудит)',not_connected:'не подключено',off:'выкл'};return st?'<span class="chip '+c+'">'+esc(L[st]||st)+'</span>':'<span class="chip">—</span>'}
function fmtDate(s){if(!s)return'';var d=new Date(s);return d.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})+' '+d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}
function views(e){var v=0;['youtube','tiktok'].forEach(function(p){if(e[p]&&e[p].stats)v+=e[p].stats.views||0});return v}

function vHome(){var s=S.settings,c=S.connections,h='';
 h+='<div class="card"><div class="row"><div><div class="big">'+S.today+' / '+s.schedule.length+'</div><div class="mu">роликов сегодня</div></div>'+sw('en',s.enabled)+'</div>';
 h+='<div class="row"><span class="mu">Расписание ('+esc(s.timezone)+')</span><span>'+S.next.map(function(n){return '<span class="chip '+(n.today?'ac':'')+'">'+n.time+'</span>'}).join(' ')+'</span></div></div>';
 h+='<div class="card"><button class="btn" id="run">▶ Создать видео сейчас</button><div style="height:10px"></div><div class="row"><select id="rf"><option value="">Формат: авто</option>'+Object.keys(FMT).map(function(k){return '<option value="'+k+'">'+FMT[k]+'</option>'}).join('')+'</select><select id="rc" style="width:90px"><option>1</option><option>2</option><option>3</option></select></div></div>';
 if(S.lastError)h+='<div class="card"><div class="row"><span class="err">⚠ '+esc(S.lastError)+'</span><button class="btn sec sm" id="ce">OK</button></div></div>';
 var q=S.history.filter(function(e){return e.tiktok&&e.tiktok.status==='awaiting_approval'});
 if(q.length){h+='<h2>TikTok · ждут одобрения</h2>';q.forEach(function(e){h+='<div class="card"><div class="t"><b>'+esc(e.title)+'</b></div><div class="mu">'+fmtDate(e.created)+' · '+esc(FMT[e.format]||e.format)+'</div><p class="mu" style="font-size:12px">Публикуется в аккаунт <b>'+esc(c.tiktok.nickname||'TikTok')+'</b>, видимость — '+esc(s.tiktok.privacy)+'. By posting, you agree to TikTok\\'s <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank">Music Usage Confirmation</a>.'+(s.tiktok.brandOrganic?' Метка: «Your brand» (продвижение своего продукта).':'')+'</p><button class="btn sm" data-ap="'+esc(e.id)+'">Опубликовать в TikTok</button></div>'})}
 h+='<h2>Подключения</h2><div class="card">';
 h+='<div class="row"><div><b>YouTube Shorts</b><div class="mu">'+(c.youtube.connected?'канал: '+esc(c.youtube.channel||'подключён'):(c.youtube.configured?'не подключён':'нужен GOOGLE_CLIENT_ID'))+'</div></div>'+(c.youtube.connected?'<button class="btn sec sm" data-dc="youtube">Отключить</button>':'<a class="btn sm" href="/oauth/youtube/start">Подключить</a>')+'</div>';
 h+='<div class="row"><div><b>TikTok</b><div class="mu">'+(c.tiktok.connected?'аккаунт: '+esc(c.tiktok.nickname||'подключён'):(c.tiktok.configured?'не подключён':'нужен TIKTOK_CLIENT_KEY'))+'</div></div>'+(c.tiktok.connected?'<button class="btn sec sm" data-dc="tiktok">Отключить</button>':'<a class="btn sm" href="/oauth/tiktok/start">Подключить</a>')+'</div>';
 h+='<div class="row"><div><b>GitHub Actions</b><div class="mu">'+esc(S.repo||'GH_REPO не задан')+'</div></div>'+(c.github.configured?'<span class="chip ok">ок</span>':'<span class="chip bad">нет токена</span>')+'</div></div>';
 h+='<h2>Последние запуски</h2><div class="card">'+(S.runs.length?S.runs.map(function(r){var st=r.conclusion||r.status;return '<div class="row"><div><b>'+esc(r.name)+'</b><div class="mu">'+fmtDate(r.created)+'</div></div>'+(r.url?'<a href="'+esc(r.url)+'" target="_blank">'+chip(st==='success'?'ok':st==='failure'?'error':st)+'</a>':chip(st))+'</div>'}).join(''):'<span class="mu">Пока пусто</span>')+'</div>';
 h+='<div style="padding:0 14px"><a class="btn sec" href="/logout" style="display:block;text-align:center;text-decoration:none">Выйти</a></div>';
 return h}

function vHist(){if(!S.history.length)return '<div class="card mu">История пуста — первый ролик появится после ближайшего запуска.</div>';
 return S.history.map(function(e){var y=e.youtube||{},t=e.tiktok||{};return '<div class="card hist"><div class="row"><span class="chip ac">'+esc(FMT[e.format]||e.format)+' · '+esc(e.level)+'</span><span class="mu">'+fmtDate(e.created)+'</span></div><div class="t">'+esc(e.title)+'</div>'+
 '<div class="row"><span>YouTube '+chip(y.status)+'</span>'+(y.url?'<a href="'+esc(y.url)+'" target="_blank">открыть</a>':'')+'</div>'+
 '<div class="row"><span>TikTok '+chip(t.status)+'</span>'+(t.status==='awaiting_approval'?'<button class="btn sm" data-ap="'+esc(e.id)+'">Опубликовать</button>':'')+'</div>'+
 '<div class="row"><span class="mu">👁 '+views(e)+' · '+esc(e.duration)+' с · тема '+esc(e.theme)+'</span></div></div>'}).join('')}

function vStats(){var st=S.stats||{},bf=st.byFormat||{},mx=0;Object.keys(bf).forEach(function(k){mx=Math.max(mx,bf[k].weight||0)});
 var h='<div class="card"><div class="row"><div><div class="big">'+((st.totals||{}).videos||S.history.length)+'</div><div class="mu">роликов всего</div></div><div><div class="big">'+((st.totals||{}).views||0)+'</div><div class="mu">просмотров</div></div></div><div class="row"><span class="mu">Обновлено: '+(st.updated?fmtDate(st.updated):'ещё нет данных')+'</span><button class="btn sec sm" id="an">Обновить</button></div></div>';
 h+='<h2>Эффективность форматов</h2><div class="card">'+Object.keys(FMT).map(function(k){var b=bf[k]||{};var w=b.weight||1;return '<div class="row" style="display:block"><div class="row" style="min-height:0"><b>'+FMT[k]+'</b><span class="mu">вес '+w+' · '+(b.count||0)+' видео'+(b.median_score!=null?' · медиана '+Math.round(b.median_score):'')+'</span></div><div class="bar"><i style="width:'+Math.round(100*w/Math.max(mx,1.3))+'%"></i></div></div>'}).join('')+'</div>';
 h+='<div class="card mu">Счёт ролика = просмотры + 10×лайки + 20×комментарии + 20×репосты. При «Автооптимизации» система чаще выбирает форматы с лучшим результатом, но продолжает тестировать новые.</div>';
 return h}

function vSet(){var s=DRAFT,h='';
 h+='<h2>Основное</h2><div class="card"><div class="row"><span>Фабрика включена</span>'+sw('s_en',s.enabled)+'</div>';
 h+='<div class="row"><span>Язык роликов</span><select id="s_lang" style="width:150px"><option value="uk"'+(s.uiLang==='uk'?' selected':'')+'>Українська</option><option value="ru"'+(s.uiLang==='ru'?' selected':'')+'>Русский</option></select></div>';
 h+='<div class="row" style="display:block"><span class="mu">Telegram-бот</span><input type="text" id="s_bot" value="'+esc(s.botHandle)+'"></div>';
 h+='<div class="row" style="display:block"><span class="mu">Текст призыва (пусто = по умолчанию)</span><input type="text" id="s_c1" value="'+esc(s.cta.line1)+'" placeholder="Більше завдань щодня у Telegram"><div style="height:8px"></div><input type="text" id="s_c3" value="'+esc(s.cta.line3)+'" placeholder="3 дні повного доступу безкоштовно"></div></div>';
 h+='<h2>Расписание</h2><div class="card">'+s.schedule.map(function(t,i){return '<div class="row"><input type="time" data-sch="'+i+'" value="'+esc(t)+'"><button class="btn sec sm" data-rm="'+i+'">✕</button></div>'}).join('')+'<div class="row"><button class="btn sec sm" id="add">+ Добавить время</button><span class="mu">'+esc(s.timezone)+'</span></div></div>';
 h+='<h2>Дизайн</h2><div class="card"><div class="themes">'+Object.keys(THEMES).map(function(k){return '<div class="th'+(s.theme===k?' on':'')+'" data-th="'+k+'"><i style="background:'+THEMES[k][1]+'"></i>'+THEMES[k][0]+'</div>'}).join('')+'</div>';
 h+='<div class="row"><span>Время на ответ: <b id="tsv">'+s.thinkSeconds+'</b> с</span><input type="range" min="3" max="8" step="1" id="s_ts" value="'+s.thinkSeconds+'"></div>';
 h+='<div class="row"><span>Звуки анимаций</span>'+sw('s_snd',s.sound.enabled)+'</div><div class="row"><span>Громкость: <b id="svv">'+s.sound.volume+'</b>%</span><input type="range" min="10" max="100" step="5" id="s_sv" value="'+s.sound.volume+'"></div></div>';
 h+='<h2>Уровни</h2><div class="card"><div class="row">'+['A1','A2','B1','B2'].map(function(l){return '<label><input type="checkbox" data-lv="'+l+'"'+(s.levels.indexOf(l)>=0?' checked':'')+'> '+l+'</label>'}).join('')+'</div></div>';
 h+='<h2>Форматы</h2><div class="card"><div class="row"><span>Автооптимизация по статистике</span>'+sw('s_ao',s.autoOptimize)+'</div>'+Object.keys(FMT).map(function(k){var f=s.formats[k];return '<div class="row"><span style="flex:1">'+FMT[k]+'</span><input type="range" min="0" max="3" step="0.1" data-fw="'+k+'" value="'+f.weight+'">'+sw('fe_'+k,f.enabled)+'</div>'}).join('')+'</div>';
 h+='<h2>Публикация</h2><div class="card"><div class="row"><span>YouTube Shorts</span>'+sw('p_yt',s.publish.youtube)+'</div><div class="row"><span>Видимость YouTube</span><select id="yt_pr" style="width:150px">'+['public','unlisted','private'].map(function(o){return '<option'+(s.youtube.privacy===o?' selected':'')+'>'+o+'</option>'}).join('')+'</select></div>';
 h+='<div class="row"><span>TikTok</span>'+sw('p_tt',s.publish.tiktok)+'</div><div class="row"><span>Режим TikTok</span><select id="tt_m" style="width:150px">'+[['inbox','Во входящие'],['direct','Сразу в ленту'],['off','Выкл']].map(function(o){return '<option value="'+o[0]+'"'+(s.tiktok.mode===o[0]?' selected':'')+'>'+o[1]+'</option>'}).join('')+'</select></div>';
 h+='<div class="row"><span>Видимость TikTok</span><select id="tt_pr" style="width:150px">'+[['PUBLIC_TO_EVERYONE','Все'],['FOLLOWER_OF_CREATOR','Подписчики'],['MUTUAL_FOLLOW_FRIENDS','Друзья'],['SELF_ONLY','Только я']].map(function(o){return '<option value="'+o[0]+'"'+(s.tiktok.privacy===o[0]?' selected':'')+'>'+o[1]+'</option>'}).join('')+'</select></div>';
 h+='<div class="row"><span>Одобрять каждый ролик вручную</span>'+sw('tt_ap',s.tiktok.requireApproval)+'</div><div class="row"><span>Метка «Your brand»</span>'+sw('tt_bo',s.tiktok.brandOrganic)+'</div>';
 h+='<div class="row"><span>Комментарии</span>'+sw('tt_c',!s.tiktok.disableComment)+'</div><div class="row"><span>Дуэты</span>'+sw('tt_d',!s.tiktok.disableDuet)+'</div><div class="row"><span>Стежки (Stitch)</span>'+sw('tt_s',!s.tiktok.disableStitch)+'</div>';
 h+='<div class="row"><span>Копия в Telegram</span>'+sw('p_tg',s.publish.telegram)+'</div></div>';
 h+='<div class="save"><button class="btn" id="save">Сохранить настройки</button></div>';
 return h}

function collect(){var s=DRAFT;function c(id){var e=document.getElementById(id);return e?e.checked:false}function v(id){var e=document.getElementById(id);return e?e.value:''}
 s.enabled=c('s_en');s.uiLang=v('s_lang');s.botHandle=v('s_bot');s.cta.line1=v('s_c1');s.cta.line3=v('s_c3');s.thinkSeconds=Number(v('s_ts'));s.sound.enabled=c('s_snd');s.sound.volume=Number(v('s_sv'));
 s.schedule=[].slice.call(document.querySelectorAll('[data-sch]')).map(function(e){return e.value}).filter(Boolean);
 s.levels=[].slice.call(document.querySelectorAll('[data-lv]')).filter(function(e){return e.checked}).map(function(e){return e.dataset.lv});
 s.autoOptimize=c('s_ao');Object.keys(FMT).forEach(function(k){s.formats[k].enabled=c('fe_'+k);s.formats[k].weight=Number(document.querySelector('[data-fw="'+k+'"]').value)});
 s.publish.youtube=c('p_yt');s.publish.tiktok=c('p_tt');s.publish.telegram=c('p_tg');s.youtube.privacy=v('yt_pr');
 s.tiktok.mode=v('tt_m');s.tiktok.privacy=v('tt_pr');s.tiktok.requireApproval=c('tt_ap');s.tiktok.brandOrganic=c('tt_bo');
 s.tiktok.disableComment=!c('tt_c');s.tiktok.disableDuet=!c('tt_d');s.tiktok.disableStitch=!c('tt_s')}

function render(){var T={home:['Главная',vHome],hist:['История',vHist],stats:['Статистика',vStats],set:['Настройки',vSet]};$('#ttl').textContent=T[TAB][0];$('#v').innerHTML=T[TAB][1]();bind()}
function bind(){var e;
 if(e=$('#en'))e.onchange=function(){DRAFT.enabled=this.checked;api('/api/settings',DRAFT).then(function(){toast(DRAFT.enabled?'Фабрика включена':'Фабрика на паузе');load()})};
 if(e=$('#run'))e.onclick=function(){var b=this;b.disabled=true;api('/api/run',{count:$('#rc').value,format:$('#rf').value}).then(function(){toast('Запущено! Видео придёт через 2–4 минуты');setTimeout(load,4000)}).catch(function(x){toast(x.message)}).then(function(){b.disabled=false})};
 if(e=$('#ce'))e.onclick=function(){api('/api/clear-error',{}).then(load)};
 if(e=$('#an'))e.onclick=function(){api('/api/analytics',{}).then(function(){toast('Аналитика запущена')}).catch(function(x){toast(x.message)})};
 document.querySelectorAll('[data-ap]').forEach(function(b){b.onclick=function(){b.disabled=true;api('/api/approve',{id:b.dataset.ap,platform:'tiktok'}).then(function(){toast('Публикуем в TikTok…');setTimeout(load,3000)}).catch(function(x){toast(x.message);b.disabled=false})}});
 document.querySelectorAll('[data-dc]').forEach(function(b){b.onclick=function(){if(confirm('Отключить '+b.dataset.dc+'?'))api('/api/disconnect',{platform:b.dataset.dc}).then(load)}});
 document.querySelectorAll('[data-th]').forEach(function(b){b.onclick=function(){collect();DRAFT.theme=b.dataset.th;render()}});
 document.querySelectorAll('[data-rm]').forEach(function(b){b.onclick=function(){collect();DRAFT.schedule.splice(Number(b.dataset.rm),1);render()}});
 if(e=$('#add'))e.onclick=function(){collect();DRAFT.schedule.push('12:00');render()};
 if(e=$('#s_ts'))e.oninput=function(){$('#tsv').textContent=this.value};
 if(e=$('#s_sv'))e.oninput=function(){$('#svv').textContent=this.value};
 if(e=$('#save'))e.onclick=function(){collect();api('/api/settings',DRAFT).then(function(){toast('Сохранено ✓');load()}).catch(function(x){toast(x.message)})};
}
document.querySelectorAll('nav button').forEach(function(b){b.onclick=function(){document.querySelectorAll('nav button').forEach(function(x){x.classList.remove('on')});b.classList.add('on');TAB=b.dataset.t;window.scrollTo(0,0);render()}});
if(location.search.indexOf('ok=')>=0){setTimeout(function(){toast('Подключено ✓')},300);history.replaceState(null,'','/')}
load();
</script></body></html>`;
