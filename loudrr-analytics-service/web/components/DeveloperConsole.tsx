"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUp,
  Check,
  Copy,
  DotsThreeVertical,
  Key,
  LockKey,
  PencilSimple,
  Plus,
  Trash,
  X,
} from "@phosphor-icons/react";
import { useAuthGate } from "./AuthGate";
import { WaitlistButton } from "./Waitlist";

const SNIPPETS: Record<string, string> = {
  curl: `curl "https://api.loudrr.com/score?userName=VitalikButerin"`,
  js: `const res = await fetch(
  "https://api.loudrr.com/score?userName=VitalikButerin"
);
const { score, tier } = await res.json();
// score: 4864, tier: "Megaphone"`,
  python: `import requests

r = requests.get(
    "https://api.loudrr.com/score",
    params={"userName": "VitalikButerin"},
)
print(r.json()["score"])  # 4864`,
};

const RESPONSE = `{
  "userName": "VitalikButerin",
  "score": 4864,
  "tier": "Megaphone",
  "rank": 2,
  "percentile": 99.9,
  "updatedAt": "2026-06-25T00:00:00Z"
}`;

type ApiTier = {
  name: string;
  price: string;
  note: string;
  rate: string;
  feats: string[];
  cta: "public" | "key" | "sales";
  featured?: boolean;
};

const TIERS: ApiTier[] = [
  {
    name: "Public",
    price: "Free",
    note: "no key required",
    rate: "60 req / min",
    feats: ["Loudrr Score", "Tier label", "Keyless — just call it"],
    cta: "public",
  },
  {
    name: "Developer",
    price: "Free",
    note: "with an API key",
    rate: "600 req / min",
    feats: ["Everything in Public", "Rank + percentile", "Top elite followers", "Score history"],
    cta: "key",
    featured: true,
  },
  {
    name: "Scale",
    price: "Custom",
    note: "for products & funds",
    rate: "Unlimited",
    feats: ["Bulk + batch lookups", "Webhooks & alerts", "New verticals early", "Priority support"],
    cta: "sales",
  },
];

export function DeveloperConsole() {
  const [tab, setTab] = useState<keyof typeof SNIPPETS>("curl");

  return (
    <div className="space-y-16">
      {/* ── the free public endpoint ── */}
      <section>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-md bg-up/15 px-2 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-up">
            GET
          </span>
          <code className="font-mono text-sm text-bone-200 sm:text-base">/score?userName=&#123;handle&#125;</code>
          <span className="rounded-full border border-white/10 px-2.5 py-1 font-mono text-[11px] text-bone-500">
            keyless · free
          </span>
        </div>
        <p className="mt-4 max-w-2xl text-bone-400">
          The public endpoint returns any account&apos;s Loudrr Score with zero auth. It&apos;s the marketing
          funnel — use it freely in apps, bots and dashboards. Add a free API key for rank, percentile and
          enriched fields.
        </p>

        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {/* request */}
          <div className="panel overflow-hidden">
            <div className="flex items-center gap-1 border-b border-white/[0.07] px-2 py-1.5">
              {(Object.keys(SNIPPETS) as (keyof typeof SNIPPETS)[]).map((k) => (
                <button
                  key={k}
                  onClick={() => setTab(k)}
                  className={`rounded-md px-3 py-1.5 font-mono text-xs transition-colors ${
                    tab === k ? "bg-white/8 text-bone-100" : "text-bone-500 hover:text-bone-200"
                  }`}
                >
                  {k === "js" ? "JavaScript" : k === "curl" ? "cURL" : "Python"}
                </button>
              ))}
              <div className="ml-auto">
                <CopyButton text={SNIPPETS[tab]} />
              </div>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-bone-200">
              <code>{SNIPPETS[tab]}</code>
            </pre>
          </div>

          {/* response */}
          <div className="panel overflow-hidden">
            <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2">
              <span className="font-mono text-xs text-bone-500">200 · application/json</span>
              <CopyButton text={RESPONSE} />
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-bone-200">
              <code>{RESPONSE}</code>
            </pre>
          </div>
        </div>
      </section>

      {/* ── API key (action-gated) ── */}
      <ApiKeyPanel />

      {/* ── tiers ── */}
      <section id="sales">
        <p className="eyebrow">Access tiers</p>
        <h2 className="mt-2 font-display text-3xl font-bold tracking-tightest sm:text-4xl">
          Free to start. Scales when you do.
        </h2>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {TIERS.map((t) => (
            <TierCard key={t.name} t={t} />
          ))}
        </div>
      </section>
    </div>
  );
}

// ── API key management (twitterapi-style UX: named keys, one-time reveal) ───

type ApiKey = {
  id: string;
  name: string;
  key: string;       // full key — table copy always copies this
  created: number;   // epoch ms
  expires: number;   // epoch ms (created + 5y)
};

const KEYS_LS = "loudrr_api_keys";

function loadKeys(): ApiKey[] {
  try {
    return JSON.parse(localStorage.getItem(KEYS_LS) || "[]");
  } catch {
    return [];
  }
}

function genKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return "ldr_live_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function truncKey(k: string): string {
  return `${k.slice(0, 16)}...${k.slice(-4)}`;
}

function relTime(ts: number): string {
  const s = Math.max(1, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function fmtDate(ts: number): string {
  return new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function ApiKeyPanel() {
  const { loggedIn, requireLogin } = useAuthGate();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [creating, setCreating] = useState(false);
  const [freshKey, setFreshKey] = useState<string | null>(null);   // one-time reveal
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<ApiKey | null>(null);

  useEffect(() => {
    setKeys(loadKeys());
  }, []);

  const save = (next: ApiKey[]) => {
    setKeys(next);
    try {
      localStorage.setItem(KEYS_LS, JSON.stringify(next));
    } catch {}
  };

  const create = (name: string) => {
    const now = Date.now();
    const k: ApiKey = {
      id: crypto.randomUUID(), name, key: genKey(),
      created: now, expires: now + 5 * 365 * 86_400_000,
    };
    save([k, ...keys]);
    setCreating(false);
    setFreshKey(k.key);
  };

  return (
    <section id="api-keys">
      <div className="panel overflow-visible p-0">
        {/* header */}
        <div className="flex flex-wrap items-center gap-3 border-b border-white/[0.07] px-5 py-4">
          <Key size={18} weight="fill" className="text-flare" />
          <h2 className="font-display text-lg font-bold tracking-tightest">Your API Keys</h2>
          <div className="ml-auto flex items-center gap-2">
            <a href="#top" className="btn-ghost !px-3 !py-1.5 text-sm">View API docs</a>
            <button
              onClick={() => (loggedIn ? setCreating(true) : requireLogin("Sign in to create your API key"))}
              className="btn-flare !px-3 !py-1.5 text-sm"
            >
              <Plus size={14} weight="bold" /> Create key
            </button>
          </div>
        </div>

        {/* body */}
        {!loggedIn ? (
          <div className="flex flex-col items-center gap-3 px-5 py-10 text-center">
            <p className="max-w-sm text-sm text-bone-400">
              A free key unlocks the enriched fields and a 10× rate limit. Sign in to create and manage keys.
            </p>
            <button onClick={() => requireLogin("Sign in to create your API key")} className="btn-flare">
              <LockKey size={16} weight="fill" /> Get your free API key
            </button>
          </div>
        ) : keys.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-bone-500">
            No keys yet — create your first key.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left">
              <thead>
                <tr className="border-b border-white/[0.07] font-mono text-[10px] uppercase tracking-[0.18em] text-bone-600">
                  <th className="px-5 py-2.5 font-medium">Name</th>
                  <th className="px-5 py-2.5 font-medium">Key</th>
                  <th className="px-5 py-2.5 font-medium">Created</th>
                  <th className="px-5 py-2.5 font-medium">Expiration</th>
                  <th className="w-10 px-3 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id} className="border-b border-white/[0.05] last:border-0">
                    <td className="px-5 py-3 text-sm font-semibold text-bone-100">{k.name}</td>
                    <td className="px-5 py-3">
                      <span className="inline-flex items-center gap-1.5">
                        <code className="font-mono text-[13px] text-bone-300">{truncKey(k.key)}</code>
                        <CopyButton text={k.key} />
                      </span>
                    </td>
                    <td className="px-5 py-3 font-mono text-[13px] text-bone-400">{relTime(k.created)}</td>
                    <td className="px-5 py-3 font-mono text-[13px] text-bone-400">{fmtDate(k.expires)}</td>
                    <td className="relative px-3 py-3">
                      <button
                        onClick={() => setMenuFor(menuFor === k.id ? null : k.id)}
                        className="grid h-7 w-7 place-items-center rounded-md text-bone-500 transition-colors hover:bg-white/5 hover:text-bone-100"
                        aria-label="Key actions"
                      >
                        <DotsThreeVertical size={17} weight="bold" />
                      </button>
                      {menuFor === k.id && (
                        <KeyMenu
                          onRename={() => { setMenuFor(null); setRenaming(k); }}
                          onDelete={() => { setMenuFor(null); save(keys.filter((x) => x.id !== k.id)); }}
                          onClose={() => setMenuFor(null)}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <AnimatePresence>
        {creating && (
          <KeyModal title="Create API Key" onClose={() => setCreating(false)}>
            <CreateKeyForm onCreate={create} />
          </KeyModal>
        )}
        {freshKey && (
          <KeyModal title="Your API Key" onClose={() => setFreshKey(null)}>
            <p className="text-center text-sm text-bone-400">
              Copy this key now. You won&apos;t be able to see it again.
            </p>
            <div className="mt-4 flex items-center gap-2 rounded-xl border border-white/15 bg-ink-900/70 px-3.5 py-3">
              <code className="min-w-0 flex-1 break-all font-mono text-sm text-bone-100">{freshKey}</code>
              <CopyButton text={freshKey} />
            </div>
            <button onClick={() => setFreshKey(null)} className="btn-flare mt-5 w-full !py-2.5">
              Done
            </button>
          </KeyModal>
        )}
        {renaming && (
          <KeyModal title="Rename key" onClose={() => setRenaming(null)}>
            <CreateKeyForm
              initial={renaming.name}
              cta="Save"
              onCreate={(name) => {
                save(keys.map((x) => (x.id === renaming.id ? { ...x, name } : x)));
                setRenaming(null);
              }}
            />
          </KeyModal>
        )}
      </AnimatePresence>
    </section>
  );
}

function KeyMenu({ onRename, onDelete, onClose }: {
  onRename: () => void; onDelete: () => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [onClose]);
  return (
    <div
      ref={ref}
      className="absolute right-2 top-11 z-30 w-40 overflow-hidden rounded-xl border border-white/10 bg-ink-900/95 py-1 shadow-2xl backdrop-blur"
    >
      <button
        onClick={onRename}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left text-sm text-bone-200 transition-colors hover:bg-white/5"
      >
        <PencilSimple size={14} /> Rename
      </button>
      <button
        onClick={onDelete}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left text-sm text-down transition-colors hover:bg-white/5"
      >
        <Trash size={14} /> Delete key
      </button>
    </div>
  );
}

function KeyModal({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] grid place-items-center bg-ink-950/70 p-4 backdrop-blur-sm"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ scale: 0.96, y: 8 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.96, y: 8 }}
        transition={{ type: "spring", stiffness: 420, damping: 32 }}
        className="w-full max-w-md rounded-2xl border border-white/10 bg-ink-900 p-6 shadow-2xl"
      >
        <div className="relative">
          <h3 className="text-center font-display text-xl font-bold tracking-tightest">{title}</h3>
          <button
            onClick={onClose}
            className="absolute -right-1 -top-1 grid h-7 w-7 place-items-center rounded-md text-bone-500 transition-colors hover:bg-white/5 hover:text-bone-100"
            aria-label="Close"
          >
            <X size={16} weight="bold" />
          </button>
        </div>
        <div className="mt-3">{children}</div>
      </motion.div>
    </motion.div>
  );
}

function CreateKeyForm({ onCreate, initial = "", cta = "Create" }: {
  onCreate: (name: string) => void; initial?: string; cta?: string;
}) {
  const [name, setName] = useState(initial);
  const ok = name.trim().length > 0;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) onCreate(name.trim());
      }}
    >
      <p className="text-center text-sm text-bone-400">This name is for your own key management.</p>
      <label className="mt-4 block font-mono text-[10px] uppercase tracking-[0.18em] text-bone-600">
        Key name
      </label>
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Add key name"
        maxLength={48}
        className="mt-1.5 w-full rounded-xl border border-white/10 bg-ink-950/60 px-3.5 py-2.5 text-sm text-bone-100 placeholder-bone-600 outline-none transition-colors focus:border-flare/50"
      />
      <button type="submit" disabled={!ok} className="btn-flare mt-5 w-full !py-2.5 disabled:cursor-not-allowed disabled:opacity-40">
        {cta}
      </button>
    </form>
  );
}

function TierCard({ t }: { t: ApiTier }) {
  const { loggedIn, requireLogin } = useAuthGate();

  return (
    <div className={`panel relative flex flex-col p-6 ${t.featured ? "ring-1 ring-flare/40" : ""}`}>
      {t.featured && (
        <span className="absolute -top-2.5 left-6 rounded-full bg-flare px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-ink-900">
          most popular
        </span>
      )}
      <div className="font-display text-xl font-bold">{t.name}</div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-mono text-3xl font-bold text-flare">{t.price}</span>
        <span className="font-mono text-xs text-bone-500">{t.note}</span>
      </div>
      <div className="mt-1 font-mono text-xs text-bone-400">{t.rate}</div>

      <ul className="mt-5 flex-1 space-y-2.5">
        {t.feats.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-bone-300">
            <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-flare" />
            {f}
          </li>
        ))}
      </ul>

      <div className="mt-6">
        {t.cta === "sales" ? (
          <a href="mailto:sales@loudrr.com?subject=Loudrr%20API%20—%20Scale%20access" className="btn-ghost w-full">
            Contact sales
          </a>
        ) : t.cta === "key" ? (
          <button
            onClick={() =>
              loggedIn
                ? document.getElementById("api-keys")?.scrollIntoView({ behavior: "smooth" })
                : requireLogin("Sign in to generate your API key")
            }
            className="btn-flare w-full"
          >
            {loggedIn ? (
              <span className="inline-flex items-center gap-1">
                Manage keys <ArrowUp size={13} />
              </span>
            ) : (
              "Get API key"
            )}
          </button>
        ) : (
          <a href="#top" className="btn-ghost w-full">
            Start calling it
          </a>
        )}
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
      className="grid h-7 w-7 place-items-center rounded-md text-bone-500 transition-colors hover:bg-white/5 hover:text-bone-100"
      aria-label="Copy"
    >
      {done ? (
        <motion.span initial={{ scale: 0.6 }} animate={{ scale: 1 }}>
          <Check size={15} weight="bold" className="text-up" />
        </motion.span>
      ) : (
        <Copy size={15} />
      )}
    </button>
  );
}
