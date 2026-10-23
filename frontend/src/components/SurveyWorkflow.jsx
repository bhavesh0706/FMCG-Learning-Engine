import { useEffect, useId, useMemo, useRef, useState } from "react";
import { getCategories, getProducts, getProductQuestions } from "../services/feedbackApi";
import { CATALOG_CHANGED, DATA_CHANGED } from "../services/appEvents";
import { getSession } from "../services/auth";
import { API_BASE_URL } from "../config";

/*
  Field survey, steps 1-3: Product -> Respondent -> Questions.
  Step 4 (the customer's audio/video feedback) happens on the Analyze page.

  Nothing is saved here. "Continue to feedback" hands the completed survey to
  App.jsx (onReady), which saves it only when the analysis starts. Until then
  every detail can still be edited.

  Props:
    onReady(draft)   the completed survey; App.jsx opens the Analyze page
    locked           true once the survey has been saved: details are read-only
    initialContext   { categoryId, productId, usagePeriod, surveyLocation, coords }
                     to start the next respondent for the same product
*/

const STEPS = ["Product", "Respondent", "Questions"];

// "Recipe v2 · Round 2": the version of the product being tested right now.
// Products with several versions in fieldwork: "2 versions" (the survey person picks one).
export function versionLabel(product) {
  const versions = product?.survey_versions || [];
  if (versions.length > 1) return `${versions.length} versions`;
  const r = product?.live_round;
  if (!r) return "";
  return [r.recipe_version ? `Recipe v${r.recipe_version}` : null, `Round ${r.number}`].filter(Boolean).join(" · ");
}

// Under the chosen product: which recipe version this survey tests, the round's
// progress and the organization's short note. The recipe itself is never shown.
function VersionCard({ round, version = null }) {
  if (!round && version) {
    const last = version.rounds[version.rounds.length - 1];
    return (
      <div className="version-card" aria-label="Product version">
        <div className="version-chips">
          <span className="version-chip">Recipe v{version.version}</span>
          <span className="round-chip">Round {last} · complete</span>
        </div>
        <p className="muted version-card-note">
          This version's round has ended. The survey is added to Round {last}, so it counts in that version's report.
        </p>
      </div>
    );
  }
  if (!round) {
    return (
      <p className="version-card is-none">
        No survey round is collecting for this product right now. This survey joins its latest round.
      </p>
    );
  }
  const pct = Math.min(100, Math.round((100 * (round.done || 0)) / Math.max(1, round.target || 1)));
  return (
    <div className="version-card" aria-label="Recipe version being tested">
      <div className="version-chips">
        {round.recipe_version && <span className="version-chip">Recipe v{round.recipe_version}</span>}
        <span className="round-chip is-live">Round {round.number} · {round.done} of {round.target}</span>
        {round.new_version && <span className="status-chip is-act">New version</span>}
      </div>
      <div className="version-progress" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
      <p className="muted version-text">
        {round.new_version
          ? `The recipe has changed: consumers taste recipe v${round.recipe_version}. This survey counts toward Round ${round.number}.`
          : `This survey counts toward Round ${round.number}.`}
      </p>
      {round.note && <p className="version-note">Note from the organization: “{round.note}”</p>}
    </div>
  );
}

const USAGE_PERIODS = [
  { value: "last_week", label: "In the last week" },
  { value: "last_month", label: "In the last month" },
  { value: "last_3_months", label: "In the last 3 months" },
  { value: "more_than_3_months", label: "More than 3 months ago" },
];

const GENDERS = ["Male", "Female", "Other", "Prefer not to say"];

const EXECUTIVE_KEY = "fmcg.survey.executiveName";

const EMPTY_RESPONDENT = { name: "", mobile: "", address: "", gender: "", age: "", location: "", consent: false };

const errorText = (err, fallback) => {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace(/^Value error, /, "");
  return err?.message || fallback;
};

// The API may return { categories: [...] }, a plain array, or an axios response.
const listFrom = (data, key) => {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.[key])) return data[key];
  if (Array.isArray(data?.data?.[key])) return data.data[key];
  if (Array.isArray(data?.data)) return data.data;
  return [];
};

const readExecutive = () => {
  try {
    return localStorage.getItem(EXECUTIVE_KEY) || "";
  } catch {
    return "";
  }
};

const rememberExecutive = (name) => {
  try {
    localStorage.setItem(EXECUTIVE_KEY, name);
  } catch {
    /* storage unavailable: nothing to remember */
  }
};

/* ---------- Small form helpers (labels are linked to their inputs) ---------- */

function Field({ label, required, hint, children, wide }) {
  const id = useId();
  return (
    <div className={`field${wide ? " is-wide" : ""}`}>
      <label htmlFor={id}>
        {label}
        {required && <span className="req" aria-hidden="true"> *</span>}
      </label>
      {children(id)}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

function ChoiceGroup({ label, options, value, onChange, compact }) {
  return (
    <div className={`choice-group${compact ? " is-compact" : ""}`} role="radiogroup" aria-label={label}>
      {options.map((opt) => {
        const v = typeof opt === "object" ? opt.value : opt;
        const text = typeof opt === "object" ? opt.label : opt;
        const on = String(value) === String(v);
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={on}
            className={`choice${on ? " is-on" : ""}`}
            onClick={() => onChange(String(v))}
          >
            {text}
          </button>
        );
      })}
    </div>
  );
}

/* ================================================================
   Workflow
   ================================================================ */

export default function SurveyWorkflow({ onReady, locked = false, initialContext = null, onActivity, active = true }) {
  const [step, setStep] = useState(initialContext?.productId ? 1 : 0);

  // Catalog
  const [categories, setCategories] = useState([]);
  const [products, setProducts] = useState([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [categoryId, setCategoryId] = useState(initialContext?.categoryId ?? "");
  const [productId, setProductId] = useState(initialContext?.productId ?? "");
  // The product version the customer tasted (recipe version), as text; "" = not chosen.
  const [versionChoice, setVersionChoice] = useState(
    initialContext?.recipeVersion != null ? String(initialContext.recipeVersion) : "");
  const [questions, setQuestions] = useState([]);
  const [questionsLoading, setQuestionsLoading] = useState(false);
  // Bumped whenever the catalog changes, so the chosen product's questions reload too.
  const [catalogVersion, setCatalogVersion] = useState(0);
  const [catalogNote, setCatalogNote] = useState("");

  // Respondent and survey context
  const [respondent, setRespondent] = useState(EMPTY_RESPONDENT);
  // Signed in: the executive is the signed-in person, always (never a name remembered
  // from someone else who used this browser before).
  const signedInName = getSession()?.user?.name || "";
  const [executiveName, setExecutiveName] = useState(() => signedInName || readExecutive());
  const [usagePeriod, setUsagePeriod] = useState(initialContext?.usagePeriod ?? "");
  const [surveyLocation, setSurveyLocation] = useState(initialContext?.surveyLocation ?? "");
  const [coords, setCoords] = useState(initialContext?.coords ?? null);
  const [gpsState, setGpsState] = useState("idle"); // idle | locating | error

  const [answers, setAnswers] = useState({});
  const [error, setError] = useState("");
  const alertRef = useRef(null);

  const product = useMemo(() => products.find((p) => String(p.id) === String(productId)) || null, [products, productId]);
  // Versions of the chosen product in fieldwork. One: used automatically. Several: the
  // survey person picks (the one collecting now is suggested). None yet: the server
  // files the survey under the product's first round.
  const versions = useMemo(() => product?.survey_versions || [], [product]);
  const autoVersion = versions.length === 1
    ? String(versions[0].version)
    : String(versions.find((v) => v.live)?.version ?? "");
  const versionValue = versions.some((v) => String(v.version) === versionChoice) ? versionChoice : autoVersion;
  const chosenVersion = versions.find((v) => String(v.version) === versionValue) || null;
  const liveForChoice = product?.live_round && (!chosenVersion || product.live_round.recipe_version === chosenVersion.version)
    ? product.live_round : null;
  const categoryProducts = useMemo(
    () => products.filter((p) => String(p.category_id) === String(categoryId)),
    [products, categoryId]
  );
  const answeredCount = questions.filter((q) => String(answers[q.id] ?? "").trim()).length;

  // Tell the app whether this form has entries, so starting another survey can ask first.
  const hasEntries =
    !locked && Boolean(respondent.name.trim() || respondent.mobile.trim() || answeredCount > 0 || (step > 0 && productId));
  useEffect(() => {
    onActivity?.(hasEntries);
  }, [hasEntries, onActivity]);

  /* ---------- Errors: shown at the top and focused, so they're never missed ---------- */

  function showError(message) {
    setError(message);
    requestAnimationFrame(() => alertRef.current?.focus());
  }

  /* ---------- Loading ---------- */

  // Latest selection, read by background reloads.
  const selectionRef = useRef({ categoryId, productId, step, locked });
  selectionRef.current = { categoryId, productId, step, locked };

  async function loadCatalog({ silent = false } = {}) {
    if (!silent) setCatalogLoading(true);
    try {
      const [c, p] = await Promise.all([getCategories(), getProducts()]);
      const cats = listFrom(c, "categories");
      const prods = listFrom(p, "products");
      setCategories(cats);
      setProducts(prods);
      setCatalogError("");

      // Keep the selection if it still exists; otherwise clear it and say why.
      const sel = selectionRef.current;
      if (!sel.locked) {
        const catGone = sel.categoryId && !cats.some((x) => String(x.id) === String(sel.categoryId));
        const prodGone = sel.productId && !prods.some((x) => String(x.id) === String(sel.productId));
        if (catGone || prodGone) {
          setProductId("");
          if (catGone) setCategoryId("");
          setStep(0);
          setCatalogNote(
            catGone
              ? "The category you chose was removed from the catalog. Choose another one."
              : "The product you chose was removed from the catalog. Choose another one."
          );
        }
      }
    } catch (err) {
      if (!silent) setCatalogError(errorText(err, "Couldn't reach the server."));
    } finally {
      if (!silent) setCatalogLoading(false);
    }
  }

  useEffect(() => {
    loadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Stay in step with the catalog: reload when it changes anywhere in the app,
  // when this page is opened again, and when the browser tab regains focus.
  useEffect(() => {
    if (!active) return undefined;
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      loadCatalog({ silent: true });
      setCatalogVersion((v) => v + 1);
    };
    refresh(); // just became visible
    const timer = setInterval(refresh, 60000);
    window.addEventListener(CATALOG_CHANGED, refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener(CATALOG_CHANGED, refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // Changes made while this page is hidden: catch up when it is shown.
  useEffect(() => {
    const mark = () => setCatalogVersion((v) => v + 1);
    window.addEventListener(DATA_CHANGED, mark);
    return () => window.removeEventListener(DATA_CHANGED, mark);
  }, []);

  useEffect(() => {
    let alive = true;
    if (!productId) {
      setQuestions([]);
      return undefined;
    }
    if (locked) return undefined; // already saved: keep exactly what was answered
    setQuestionsLoading(true);
    getProductQuestions(productId)
      .then((data) => {
        if (!alive) return;
        const list = listFrom(data, "questions");
        setQuestions(list);
        // Keep answers to questions that still exist (an admin may have edited the list).
        setAnswers((prev) => Object.fromEntries(list.map((q) => [q.id, prev[q.id] ?? ""])));
      })
      .catch((err) => alive && showError(errorText(err, "Couldn't load the questions for this product.")))
      .finally(() => alive && setQuestionsLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, catalogVersion, active]);

  /* ---------- Validation per step ---------- */

  const validateProduct = () => {
    if (!categoryId) return "Choose a category.";
    if (!productId || !product) return "Choose the product the customer used.";
    if (!product.survey_ready) return `${product.name} isn't ready for surveys yet: it needs 5 to 10 questions.`;
    if (versions.length > 1 && !chosenVersion) return "Choose the version of the product the customer tasted.";
    return "";
  };

  const validateRespondent = () => {
    if (!respondent.name.trim()) return "Enter the respondent's name.";
    const digits = respondent.mobile.replace(/\D/g, "");
    if (respondent.mobile.trim() && (digits.length < 10 || digits.length > 13))
      return "Enter a valid mobile number (10 digits, optionally with country code).";
    if (respondent.age !== "") {
      const age = Number(respondent.age);
      if (!Number.isInteger(age) || age < 1 || age > 120) return "Enter an age between 1 and 120.";
    }
    if (!respondent.location.trim()) return "Enter where the respondent lives (city or locality).";
    if (!executiveName.trim()) return "Enter your name as the survey executive.";
    if (!usagePeriod) return "Choose when the customer used the product.";
    if (!surveyLocation.trim()) return "Enter where this survey is taking place.";
    if (!respondent.consent) return "The respondent's consent is required before continuing.";
    return "";
  };

  const validateQuestions = () => {
    const missing = questions.find((q) => q.required && !String(answers[q.id] ?? "").trim());
    return missing ? `Answer question ${questions.indexOf(missing) + 1}: "${missing.text}"` : "";
  };

  /* ---------- Handing the completed survey to the Analyze page ---------- */

  function finish() {
    // Re-check every step: details may have been edited after coming back.
    for (const [index, check] of [validateProduct, validateRespondent, validateQuestions].entries()) {
      const problem = check();
      if (problem) {
        setStep(index);
        showError(problem);
        return;
      }
    }
    if (!signedInName) rememberExecutive(executiveName.trim());
    onReady?.({
      productId: Number(productId),
      productName: product?.name || "",
      // The product (recipe) version and round this survey tests: the chosen version's
      // live round, else its latest round (the server files it the same way).
      recipeVersion: chosenVersion?.version ?? product?.live_round?.recipe_version ?? null,
      round: liveForChoice?.number ?? (chosenVersion ? chosenVersion.rounds[chosenVersion.rounds.length - 1] : null)
        ?? product?.live_round?.number ?? null,
      respondentName: respondent.name.trim(),
      questionCount: questions.length,
      respondent: {
        name: respondent.name.trim(),
        mobile: respondent.mobile.trim() || null,
        address: respondent.address.trim() || null,
        gender: respondent.gender || null,
        age: respondent.age === "" ? null : Number(respondent.age),
        location: respondent.location.trim(),
        consent_given: true,
      },
      response: {
        product_id: Number(productId),
        // Saved with the survey; omitted when the product has no version in fieldwork yet.
        ...(chosenVersion ? { recipe_version: chosenVersion.version } : {}),
        executive_name: (signedInName || executiveName).trim(),
        usage_period: usagePeriod,
        location: surveyLocation.trim(),
        latitude: coords?.latitude ?? null,
        longitude: coords?.longitude ?? null,
      },
      answers: questions.map((q) => ({ question_id: q.id, value: String(answers[q.id] ?? "").trim() })),
      // Kept for "Next respondent, same product".
      context: { categoryId, productId, recipeVersion: chosenVersion?.version ?? null, usagePeriod,
        surveyLocation: surveyLocation.trim(), coords },
    });
  }

  function next() {
    setError("");
    if (locked) {
      // Already saved: just move through the read-only steps.
      if (step < 2) setStep((s) => s + 1);
      else onReady?.(null);
      return;
    }
    if (step === 2) {
      finish();
      return;
    }
    const problem = [validateProduct, validateRespondent][step]();
    if (problem) {
      showError(problem);
      return;
    }
    setStep((s) => s + 1);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function back() {
    setError("");
    setStep((s) => Math.max(0, s - 1));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---------- GPS ---------- */

  function captureLocation() {
    if (!navigator.geolocation) {
      setGpsState("error");
      return;
    }
    setGpsState("locating");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setCoords({
          latitude: Number(pos.coords.latitude.toFixed(6)),
          longitude: Number(pos.coords.longitude.toFixed(6)),
          accuracy: Math.round(pos.coords.accuracy),
        });
        setGpsState("idle");
      },
      () => setGpsState("error"),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  }

  /* ================================================================
     Render
     ================================================================ */

  return (
    <section className="survey" aria-labelledby="survey-title">
      <header className="survey-head">
        <h1 id="survey-title">Take survey</h1>
        <p className="survey-sub">
          {product && step > 0
            ? `${product.name}${versionLabel(product) ? ` · ${versionLabel(product)}` : ""}`
            : "Product, respondent and answers here; the customer's recording comes next."}
        </p>
      </header>

      <ol className="stepper" aria-label="Survey progress">
        {[...STEPS, "Feedback"].map((label, i) => {
          const done = i < step;
          const current = i === step;
          return (
            <li
              key={label}
              className={`${done ? "is-done" : ""}${current ? " is-current" : ""}`}
              aria-current={current ? "step" : undefined}
            >
              <span className="stepper-dot" aria-hidden="true">{done ? "✓" : i + 1}</span>
              <span className="stepper-label">{label}</span>
            </li>
          );
        })}
      </ol>

      {error && (
        <div ref={alertRef} tabIndex={-1} role="alert" className="alert alert-error survey-alert">
          {error}
        </div>
      )}

      {locked && (
        <p className="locked-note">
          This survey has been saved with its analysis, so its details can no longer be changed. Start a new survey
          from the Analyze page to survey another respondent.
        </p>
      )}

      {/* An error disappears as soon as the executive starts fixing the form. */}
      <div
        className="panel survey-panel"
        onInput={() => error && setError("")}
        onClick={(e) => error && e.target.closest?.(".choice, input[type=checkbox]") && setError("")}
      >
        {/* ---------------- 1. Product ---------------- */}
        {step === 0 && (
          <>
            <h2 className="survey-step-title">Which product did the customer use?</h2>

            {catalogNote && (
              <p className="survey-catalog-note" role="status">{catalogNote}</p>
            )}

            {!catalogLoading && (catalogError || categories.length === 0) && (
              <div className="catalog-empty" role="status">
                <strong>{catalogError ? "The product list couldn't be loaded" : "No products have been set up yet"}</strong>
                <p>
                  {catalogError
                    ? `${catalogError} Check that the backend is running at ${API_BASE_URL}.`
                    : "The catalog is empty. An admin needs to add categories and products, or load the sample catalog with: python -m app.database.seed_catalog"}
                </p>
                <button type="button" className="btn btn-secondary" onClick={loadCatalog}>
                  Try again
                </button>
              </div>
            )}

            <fieldset className="form-section" disabled={locked}>
              <div className="form-grid">
                <Field label="Category" required>
                  {(id) => (
                    <select
                      id={id}
                      className="input"
                      value={categoryId}
                      disabled={catalogLoading}
                      onChange={(e) => {
                        setCatalogNote("");
                        setCategoryId(e.target.value);
                        setProductId("");
                        setVersionChoice("");
                      }}
                    >
                      <option value="">{catalogLoading ? "Loading…" : "Choose a category"}</option>
                      {categories.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  )}
                </Field>
                <Field label="Product" required>
                  {(id) => (
                    <select
                      id={id}
                      className="input"
                      value={productId}
                      disabled={!categoryId}
                      onChange={(e) => { setProductId(e.target.value); setVersionChoice(""); }}
                    >
                      <option value="">{categoryId ? "Choose a product" : "Choose a category first"}</option>
                      {categoryProducts.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}{versionLabel(p) ? ` (${versionLabel(p)}${p.live_round?.new_version ? ", new version" : ""})` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
                {versions.length > 1 && (
                  <Field label="Version" required>
                    {(id) => (
                      <select id={id} className="input" value={versionValue}
                        onChange={(e) => setVersionChoice(e.target.value)}>
                        {!autoVersion && <option value="">Choose the version tasted</option>}
                        {versions.map((v) => (
                          <option key={v.version} value={String(v.version)}>
                            Version {v.version} · Round{v.rounds.length > 1 ? "s" : ""} {v.rounds.join(", ")}
                            {v.live ? " (collecting now)" : " (complete)"}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                )}
              </div>
            </fieldset>

            {categoryId && !catalogLoading && categoryProducts.length === 0 && (
              <p className="field-hint catalog-hint">No active products in this category yet.</p>
            )}

            {product && (
              <div className="product-summary">
                <div className="product-summary-top">
                  <strong>{product.name}</strong>
                  <span className={`status-chip ${product.survey_ready ? "is-ok" : "is-warn"}`}>
                    {product.survey_ready ? "Ready for surveys" : "Needs 5–10 questions"}
                  </span>
                </div>
                <p className="muted">
                  {product.question_count} questions · {product.responses_collected}{" "}
                  {Number(product.responses_collected) === 1 ? "survey" : "surveys"} so far
                </p>
                <VersionCard round={liveForChoice} version={liveForChoice ? null : chosenVersion} />
              </div>
            )}
          </>
        )}

        {/* ---------------- 2. Respondent ---------------- */}
        {step === 1 && (
          <>
            {/* The step indicator already says "Respondent"; the heading stays for screen readers. */}
            <h2 className="visually-hidden">Respondent details</h2>

            <div className="respondent-layout">
              <fieldset className="form-section" disabled={locked}>
                <legend>Customer</legend>
                <div className="form-grid cols-2">
                  <Field label="Full name" required>
                    {(id) => (
                      <input id={id} className="input" autoComplete="off" value={respondent.name}
                        onChange={(e) => setRespondent({ ...respondent, name: e.target.value })} />
                    )}
                  </Field>
                  <Field label="Mobile (optional)">
                    {(id) => (
                      <input id={id} className="input" type="tel" inputMode="tel" autoComplete="off" placeholder="98765 43210"
                        title="Never shown in product reports"
                        value={respondent.mobile} onChange={(e) => setRespondent({ ...respondent, mobile: e.target.value })} />
                    )}
                  </Field>
                  <Field label="Gender">
                    {(id) => (
                      <select id={id} className="input" value={respondent.gender}
                        onChange={(e) => setRespondent({ ...respondent, gender: e.target.value })}>
                        <option value="">Choose</option>
                        {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
                      </select>
                    )}
                  </Field>
                  <Field label="Age">
                    {(id) => (
                      <input id={id} className="input" type="number" inputMode="numeric" min="1" max="120"
                        value={respondent.age} onChange={(e) => setRespondent({ ...respondent, age: e.target.value })} />
                    )}
                  </Field>
                  <Field label="City or locality" required>
                    {(id) => (
                      <input id={id} className="input" placeholder="e.g. Andheri, Mumbai" value={respondent.location}
                        onChange={(e) => setRespondent({ ...respondent, location: e.target.value })} />
                    )}
                  </Field>
                  <Field label="Address (optional)">
                    {(id) => (
                      <input id={id} className="input" value={respondent.address}
                        onChange={(e) => setRespondent({ ...respondent, address: e.target.value })} />
                    )}
                  </Field>
                </div>
              </fieldset>

              <fieldset className="form-section" disabled={locked}>
                <legend>This survey</legend>
                <div className="form-grid cols-2">
                  {signedInName ? (
                    <Field label="Surveyed by">
                      {(id) => (
                        <input id={id} className="input input-readonly" value={signedInName} readOnly
                          title="The signed-in account. Sign out to survey as someone else." />
                      )}
                    </Field>
                  ) : (
                    <Field label="Your name (executive)" required>
                      {(id) => (
                        <input id={id} className="input" value={executiveName} title="Remembered on this device"
                          onChange={(e) => setExecutiveName(e.target.value)} />
                      )}
                    </Field>
                  )}
                  <Field label="Survey location" required>
                    {(id) => (
                      <input id={id} className="input" placeholder="e.g. D-Mart, Andheri West" value={surveyLocation}
                        onChange={(e) => setSurveyLocation(e.target.value)} />
                    )}
                  </Field>
                  <div className="field is-wide">
                    <span className="field-label">
                      When did they use {product?.name || "the product"}?<span className="req" aria-hidden="true"> *</span>
                    </span>
                    <ChoiceGroup label="Usage period" options={USAGE_PERIODS} value={usagePeriod} onChange={setUsagePeriod} compact />
                  </div>
                  <div className="field is-wide">
                    <span className="field-label">GPS location (optional)</span>
                    <div className="gps-row">
                      <button type="button" className="btn btn-secondary btn-sm" onClick={captureLocation} disabled={gpsState === "locating"}>
                        {gpsState === "locating" ? "Finding location…" : coords ? "Update location" : "Use my current location"}
                      </button>
                      <span className="muted gps-status" aria-live="polite">
                        {coords
                          ? `${coords.latitude}, ${coords.longitude} (±${coords.accuracy} m)`
                          : gpsState === "error"
                          ? "Location unavailable: allow access, or skip."
                          : ""}
                      </span>
                    </div>
                  </div>
                </div>
              </fieldset>
            </div>

            <label className={`consent${respondent.consent ? " is-on" : ""}`}>
              <input type="checkbox" checked={respondent.consent} disabled={locked}
                onChange={(e) => setRespondent({ ...respondent, consent: e.target.checked })} />
              <span>
                <strong>The respondent agrees</strong> to the use of their details, answers and recording to analyze
                feedback on this product. Name, mobile and address never appear in reports.
                <span className="req" aria-hidden="true"> *</span>
              </span>
            </label>
          </>
        )}

        {/* ---------------- 3. Questions ---------------- */}
        {step === 2 && (
          <>
            <div className="survey-step-head">
              <h2 className="survey-step-title">Questions about {product?.name}</h2>
              <span className="answered-count" aria-live="polite">
                {answeredCount} of {questions.length} answered
              </span>
            </div>

            {questionsLoading ? (
              <p className="muted">Loading questions…</p>
            ) : (
              <fieldset className="form-section" disabled={locked}>
                <ol className="question-list">
                  {questions.map((q, i) => {
                    const value = answers[q.id] ?? "";
                    const set = (v) => setAnswers((a) => ({ ...a, [q.id]: v }));
                    const labelId = `q-${q.id}-label`;
                    return (
                      <li key={q.id} className={`question-card${String(value).trim() ? " is-answered" : ""}`}>
                        <p className="question-text" id={labelId}>
                          <span className="question-num">{i + 1}</span>
                          <span>
                            {q.text}
                            {q.required && <span className="req" aria-hidden="true"> *</span>}
                          </span>
                        </p>
                        {q.question_type === "rating" && (
                          <div className="rating-scale">
                            <ChoiceGroup label={q.text} options={["1", "2", "3", "4", "5"]} value={value} onChange={set} compact />
                            <div className="rating-ends" aria-hidden="true">
                              <span>Poor</span>
                              <span>Excellent</span>
                            </div>
                          </div>
                        )}
                        {q.question_type === "yes_no" && (
                          <ChoiceGroup label={q.text} options={["Yes", "No"]} value={value} onChange={set} compact />
                        )}
                        {q.question_type === "single_choice" && (
                          <ChoiceGroup label={q.text} options={Array.isArray(q.options) ? q.options : []} value={value} onChange={set} />
                        )}
                        {q.question_type === "text" && (
                          <textarea aria-labelledby={labelId} className="input" rows={3}
                            placeholder="Write down what the customer says" value={value} onChange={(e) => set(e.target.value)} />
                        )}
                      </li>
                    );
                  })}
                </ol>
              </fieldset>
            )}
          </>
        )}
      </div>

      {/* Navigation: Back is always available */}
      <div className="survey-actions">
        {step > 0 && (
          <button type="button" className="btn btn-secondary" onClick={back}>
            Back
          </button>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={next}
          disabled={step === 0 && catalogLoading}
        >
          {step < 2 ? "Continue" : locked ? "Back to the analysis" : "Continue to feedback"}
        </button>
      </div>
    </section>
  );
}