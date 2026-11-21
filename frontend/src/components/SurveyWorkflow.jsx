import { useEffect, useId, useMemo, useRef, useState } from "react";
import { getCategories, getProducts, getProductQuestions, reverseGeocode } from "../services/feedbackApi";
import { CATALOG_CHANGED, DATA_CHANGED } from "../services/appEvents";
import { getSession } from "../services/auth";
import { myActionPlans } from "../services/orgApi";
import { getMasters } from "../services/nextApi";
import { fillArray, lovChildren, lovMatch, lovValues, onLovLoaded } from "../services/lov";
import { API_BASE_URL } from "../config";
import { captureFix, describeFix, fixFields, isPlaceholder, keepFix, keptFix, placeholderFor } from "../services/gps";
import Select from "./Select";

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

const STEPS = ["Product", "Respondent", "Questions"]; // the GPS is taken on its own and shown on the feedback step

// "Recipe v2 · Round 2": the version of the product being tested right now.
// Products with several versions in fieldwork: "2 versions" (the survey person picks one).
export function versionLabel(product) {
  const versions = product?.survey_versions || [];
  if (versions.length > 1) return `${versions.length} versions`;
  const r = product?.live_round;
  if (!r) return "";
  return [r.recipe_version ? `Recipe v${r.recipe_version}` : null, `Survey ${r.number}`].filter(Boolean).join(" · ");
}

// "How long have you been consuming / using <product>?" (the stored codes are unchanged)
const USAGE_PERIODS = [
  { value: "last_week", label: "Less than 7 days" },
  { value: "last_month", label: "For the past 1 month" },
  { value: "last_3_months", label: "For the last 3 months" },
  { value: "more_than_3_months", label: "For more than 3 months" },
];

// "consuming" for food and drink, "using" for everything else (shampoo, soap, detergent ...).
// The category name decides first, then the product name; unclear = "using".
const EDIBLE = /\b(food|beverage|drink|dairy|milk|cereal|snack|confection|chocolate|juice|tea|coffee|water|biscuit|bakery|bread|jelly|jam|sauce|spice|masala|ghee|butter|cheese|curd|yogh?urt|lassi|noodle|pasta|granola|flakes|oats|candy|nutrition|edible|ice ?cream|frozen|rice|atta|flour|dal|honey|pickle|sweets?)/i;
const NON_EDIBLE = /\b(soap|shampoo|detergent|lotion|cosmetic|personal care|home care|cleaner|cleaning|toothpaste|deodorant|perfume|fragrance|hair|skin|laundry|dishwash|tissue|diaper|sanitary|repellent|polish|insecticide|household|beauty|make ?up)/i;
export function usageVerb(productName, categoryName) {
  if (EDIBLE.test(categoryName || "")) return "consuming";
  if (NON_EDIBLE.test(`${productName || ""} ${categoryName || ""}`)) return "using";
  return EDIBLE.test(productName || "") ? "consuming" : "using";
}

const GENDERS = ["Male", "Female", "Other", "Prefer not to say"];
// From the database lists (LOV USAGE_PERIOD, GENDER) once loaded; the values above are the fallback.
onLovLoaded(() => {
  fillArray(USAGE_PERIODS, "USAGE_PERIOD", (v) => ({ value: v.code, label: v.label }));
  fillArray(GENDERS, "GENDER", (v) => v.label);
});


const EXECUTIVE_KEY = "fmcg.survey.executiveName";

const EMPTY_RESPONDENT = {
  name: "", mobile: "", address: "", gender: "", age: "", location: "", consent: false,
  // Next update: country, respondent type, occupation and class (from the master lists)
  country: "India", respondent_type: "", occupation: "", sec_class: "",
  // Country -> State -> City from the LOV lists (CustomerPlace below)
  state: "", city: "",
};

/* Multiple choice: tick any number of options; the answer is "A | B". */
function MultiChoice({ label, options, value, onChange }) {
  const picked = String(value || "").split("|").map((v) => v.trim()).filter(Boolean);
  const toggle = (o) => {
    const next = picked.includes(o) ? picked.filter((p) => p !== o) : [...picked, o];
    onChange(options.filter((x) => next.includes(x)).join(" | "));
  };
  return (
    <div className="choice-group is-compact" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o} type="button" className={`choice${picked.includes(o) ? " is-on" : ""}`} aria-pressed={picked.includes(o)} onClick={() => toggle(o)}>
          {picked.includes(o) ? "✓ " : ""}{o}
        </button>
      ))}
    </div>
  );
}

const errorText = (err, fallback) => {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof d.message === "string") return d.message; // e.g. a recipe locked by a collecting survey
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

/* The customer's place: Country -> State -> City from the LOV lists COUNTRY / STATE / CITY
   (each list inside the previous one), then the current location, which the address
   lookup (GPS + geocoder) can fill in together with the country, state and city.
   A country without states in the list (added by an admin) gets free-text fields. */
const OTHER_CITY = "__other_city__";

function CustomerPlace({ respondent, setRespondent, onLocated }) {
  const countries = lovValues("COUNTRY") || [];
  const country = lovMatch(countries, respondent.country);
  const states = lovChildren("STATE", country?.id);
  const state = lovMatch(states, respondent.state);
  const cities = lovChildren("CITY", state?.id);
  const [gps, setGps] = useState({ status: "idle", message: "" });
  // "Other city": a city that isn't in the state's list, typed by hand (or found by the lookup).
  const [otherCity, setOtherCity] = useState(false);
  const cityInList = lovMatch(cities, respondent.city);
  const typingCity = cities.length > 0 && (otherCity || Boolean(respondent.city && !cityInList));
  const set = (patch) => setRespondent((r) => ({ ...r, ...patch }));

  // "Use current location": the GPS (on a device that has one) works without internet;
  // the address lookup needs the server. Offline, the position is kept with the survey
  // and the place is filled in from it once back online (only what is still empty).
  const pendingFix = useRef(null);

  const fillFrom = async (found, onlyEmpty = false) => {
    setGps({ status: "resolving", message: "" });
    try {
      const place = (await reverseGeocode(found.latitude, found.longitude)) || {};
      const c = lovMatch(lovValues("COUNTRY") || [], place.country);
      const stateList = lovChildren("STATE", c?.id);
      const st = lovMatch(stateList, place.state);
      // The lookup gives several names (city, town, district ...): the first one in the state's city list.
      const cityList = lovChildren("CITY", st?.id);
      const ci = [place.city, ...(place.places || [])].map((n) => lovMatch(cityList, n)).find(Boolean) || null;
      const where = {
        country: c?.label || place.country || "",
        // A country with a state list: only a value of the list; otherwise the looked-up name.
        state: st?.label || (stateList.length ? "" : place.state || ""),
        // Not in the list: keep the looked-up name (shown as "Other city"), so the city is never lost.
        city: ci?.label || place.city || "",
      };
      const area = place.locality || place.label || "";
      // Chosen or typed by hand while offline: kept.
      setRespondent((r) => ({
        ...r,
        ...(onlyEmpty && r.state && r.city ? {} : where),
        ...(onlyEmpty && (r.location || "").trim() && !isPlaceholder(r.location) ? {} : { location: area }),
      }));
      pendingFix.current = null;
      setOtherCity(false);
      const missing = st && !ci && place.city
        ? ` "${place.city}" isn't in the city list of ${st.label}, so it's filled in as typed. Pick a listed city if one fits, or ask an admin to add it.`
        : c && !st && place.state ? ` Choose the state.` : "";
      // OpenStreetMap knows no town at this spot: the city came from the district's name.
      const fromDistrict = ci && ["state_district", "county"].includes(place.city_from)
        ? ` The city was taken from the district (${place.city}): check it with the customer.`
        : "";
      setGps({ status: missing ? "warn" : "idle", message: `Found: ${place.label || "your location"}.${missing}${fromDistrict}` });
      onLocated?.(found, place);
    } catch (err) {
      setGps({ status: "warn", message: err?.response?.data?.detail || "Couldn't find the address right now (the GPS position is kept). Choose the place and type the area." });
    }
  };

  const locate = async () => {
    setGps({ status: "locating", message: "" });
    let found;
    try {
      found = await captureFix(); // { latitude, longitude, accuracy, captured_at, source }
    } catch (err) {
      setGps({ status: "error", message: `${err.message}`.replace("type the location", "choose the place by hand") });
      return;
    }
    if (navigator.onLine === false) {
      onLocated?.(found, null); // the survey keeps the position (offline too)
      pendingFix.current = found;
      // No address offline: the position is the area for now (replaced when back online,
      // or by the server when the survey is saved).
      setRespondent((r) => ((r.location || "").trim() && !isPlaceholder(r.location) ? r : { ...r, location: placeholderFor(found) }));
      setGps({
        status: "warn",
        message: `Offline: the GPS position (${describeFix(found)}) is used as the area. It becomes the area's name when you're back online. Choose the state and city if they're empty.`,
      });
      return;
    }
    await fillFrom(found);
  };

  // Back online with a position taken offline: fill in the place from it.
  useEffect(() => {
    const onOnline = () => {
      if (pendingFix.current) fillFrom(pendingFix.current, true);
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  });

  const select = (id, value, options, onChange, what) => (
    <Select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{`Choose ${what}`}</option>
      {options.map((o) => <option key={o.id} value={o.label}>{o.label}</option>)}
    </Select>
  );
  const text = (id, value, onChange, placeholder) => (
    <input id={id} className="input" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
  );
  const busy = gps.status === "locating" || gps.status === "resolving";

  // Captured automatically: the country, state and city come from the device's location; the
  // survey person only checks them (Change opens the lists, also used when capture fails).
  const [manual, setManual] = useState(false);
  const autoTried = useRef(false);
  useEffect(() => {
    if (!autoTried.current && !(respondent.city || "").trim() && !(respondent.location || "").trim()) {
      autoTried.current = true;
      locate();
    }
  }); // eslint-disable-line react-hooks/exhaustive-deps
  const placed = (respondent.country && respondent.state && respondent.city) || isPlaceholder(respondent.location);
  if (placed && !manual) {
    return (
      <div className="field is-wide">
        <span className="field-label">Customer's place (from this device's location)</span>
        <div className="place-summary">
          <span className="place-pin" aria-hidden="true">📍</span>
          <span className="place-text">
            <strong>{respondent.location && !isPlaceholder(respondent.location) ? `${respondent.location}, ` : ""}{[respondent.city, respondent.state, respondent.country].filter(Boolean).join(", ")
              || "GPS position kept: the address is added when you're back online"}</strong>
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={locate} disabled={busy}>{busy ? "Finding…" : "Update"}</button>
          <button type="button" className="link-btn" onClick={() => setManual(true)}>Change</button>
        </div>
        {gps.message && <span className={`gps-status ${gps.status === "warn" ? "is-warn" : "muted"}`}>{gps.message}</span>}
      </div>
    );
  }

  return (
    <>
      <Field label="Country" required>
        {(id) => countries.length
          ? select(id, country?.label || "", countries, (v) => set({ country: v, state: "", city: "" }), "country")
          : text(id, respondent.country, (v) => set({ country: v }), "e.g. India")}
      </Field>
      <Field label="State" required>
        {(id) => states.length
          ? select(id, state?.label || "", states, (v) => { setOtherCity(false); set({ state: v, city: "" }); }, "state")
          : text(id, respondent.state, (v) => set({ state: v, city: "" }), country ? "Type the state" : "Choose the country first")}
      </Field>
      <Field label="City" required hint={typingCity ? "Not in the city list. An admin can add it in Catalog > Master lists." : null}>
        {(id) => !cities.length
          ? text(id, respondent.city, (v) => set({ city: v }), state || !states.length ? "Type the city" : "Choose the state first")
          : typingCity
          ? (
            <div className="gps-row">
              {text(id, respondent.city, (v) => set({ city: v }), "Type the city")}
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setOtherCity(false); set({ city: "" }); }}>
                Choose from list
              </button>
            </div>
          )
          : (
            <Select id={id} className="input" value={cityInList?.label || ""}
              onChange={(e) => {
                if (e.target.value === OTHER_CITY) {
                  setOtherCity(true);
                  set({ city: "" });
                } else set({ city: e.target.value });
              }}>
              <option value="">Choose city</option>
              {cities.map((o) => <option key={o.id} value={o.label}>{o.label}</option>)}
              <option value={OTHER_CITY}>Other city (type it)</option>
            </Select>
          )}
      </Field>
      <Field label="Current location (area or locality)" required wide>
        {(id) => (
          <>
            <div className="gps-row">
              <input id={id} className="input" placeholder="e.g. Andheri West, or use the button" value={respondent.location}
                onChange={(e) => set({ location: e.target.value })} />
              <button type="button" className="btn btn-secondary btn-sm" onClick={locate} disabled={busy}>
                {gps.status === "locating" ? "Finding location…" : gps.status === "resolving" ? "Finding the address…" : "Use current location"}
              </button>
            </div>
            <span className={`gps-status ${gps.status === "error" ? "is-error" : gps.status === "warn" ? "is-warn" : "muted"}`} aria-live="polite">
              {gps.message || "Fills in the country, state, city and area from this device's location."}
            </span>
          </>
        )}
      </Field>
    </>
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
  // Action plans: a survey person surveys only the cities allocated to them (the server checks too)
  const [myPlans, setMyPlans] = useState([]);
  useEffect(() => {
    if (getSession()?.user?.role !== "executive") return;
    myActionPlans().then((r) => setMyPlans(r.plans || [])).catch(() => setMyPlans([]));
  }, []);
  const [executiveName, setExecutiveName] = useState(() => signedInName || readExecutive());
  const [usagePeriod, setUsagePeriod] = useState(initialContext?.usagePeriod ?? "");
  // Internal (staff, family) or External (real customers) survey
  const [audience, setAudience] = useState(initialContext?.audience ?? "external");
  // Master lists: occupation, class (SEC), respondent type
  const [masters, setMasters] = useState({ occupation: [], sec_class: [], respondent_type: [] });
  useEffect(() => {
    getMasters().then(setMasters).catch(() => {});
  }, []);
  const [surveyLocation, setSurveyLocation] = useState(initialContext?.surveyLocation ?? "");
  // GPS fix of this survey: { latitude, longitude, accuracy, captured_at, source } (services/gps.js).
  // Kept in this browser until the survey is saved, so a reload or lost connection keeps it.
  const [coords, setCoordsState] = useState(() => initialContext?.coords ?? keptFix());
  const setCoords = (fix) => {
    setCoordsState(fix);
    keepFix(fix);
  };
  const [gpsState, setGpsState] = useState("idle"); // idle | locating | resolving | error
  const [, setGpsMessage] = useState("");
  // The Survey location text came from GPS (so a new GPS reading may replace it, but typed text never is).
  const [autoFilled, setAutoFilled] = useState(false);
  const [surveyPlace, setSurveyPlace] = useState(null); // the address looked up from the GPS (country, state, city)

  const [answers, setAnswers] = useState({});
  const [error, setError] = useState("");
  const alertRef = useRef(null);

  const product = useMemo(() => products.find((p) => String(p.id) === String(productId)) || null, [products, productId]);
  const myPlan = myPlans.find((p) => String(p.product_id) === String(productId)) || null;
  const myCities = myPlan?.cities || [];
  const verb = usageVerb(product?.name, categories.find((c) => String(c.id) === String(product?.category_id ?? categoryId))?.name);
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
    if (myCities.length) {
      const city = (respondent.city || "").trim().toLowerCase();
      if (!city) return `Choose the customer's city: you survey in ${myCities.join(", ")} only.`;
      if (!myCities.some((c) => c.toLowerCase() === city))
        return `You survey customers in ${myCities.join(", ")} only (action plan “${myPlan.plan_name}”).`;
    }
    const digits = respondent.mobile.replace(/\D/g, "");
    if (respondent.mobile.trim() && (digits.length < 10 || digits.length > 13))
      return "Enter a valid mobile number (10 digits, optionally with country code).";
    if (respondent.age !== "") {
      const age = Number(respondent.age);
      if (!Number.isInteger(age) || age < 1 || age > 120) return "Enter an age between 1 and 120.";
    }
    // Offline with a GPS position: the place is filled in from it once back online, so it isn't asked.
    const placeFromGps = isPlaceholder(respondent.location) && coords;
    if (!placeFromGps) {
      if (!(respondent.country || "").trim()) return "Choose the customer's country.";
      if (!(respondent.state || "").trim()) return "Choose the customer's state.";
      if (!(respondent.city || "").trim()) return "Choose the customer's city.";
    }
    if (!respondent.location.trim()) return "Enter the customer's current location (area or locality), or use the location button.";
    if (!executiveName.trim()) return "Enter your name as the survey executive.";
    if (!usagePeriod) return `Choose how long the customer has been ${verb} the product.`;
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
      // Shown on the feedback step as "Current location".
      place: { label: surveyLocation.trim(), city: surveyPlace?.city || "", state: surveyPlace?.state || "", country: surveyPlace?.country || "", coords },
      respondent: {
        name: respondent.name.trim(),
        mobile: respondent.mobile.trim() || null,
        address: respondent.address.trim() || null,
        gender: respondent.gender || null,
        age: respondent.age === "" ? null : Number(respondent.age),
        location: respondent.location.trim(),
        country: (respondent.country || "").trim() || null,
        state: (respondent.state || "").trim() || null,
        city: (respondent.city || "").trim() || null,
        respondent_type: respondent.respondent_type || null,
        occupation: respondent.occupation || null,
        sec_class: respondent.sec_class || null,
        consent_given: true,
      },
      response: {
        product_id: Number(productId),
        // Saved with the survey; omitted when the product has no version in fieldwork yet.
        ...(chosenVersion ? { recipe_version: chosenVersion.version } : {}),
        executive_name: (signedInName || executiveName).trim(),
        usage_period: usagePeriod,
        audience,
        location: surveyLocation.trim(),
        // Same fields as the mobile apps: position, accuracy, original capture time, platform.
        ...fixFields(coords),
      },
      answers: questions.map((q) => ({ question_id: q.id, value: String(answers[q.id] ?? "").trim() })),
      // Kept for "Next respondent, same product".
      context: { categoryId, productId, recipeVersion: chosenVersion?.version ?? null, usagePeriod, audience,
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

  // Coordinates -> address for "Survey location" (needs the server).
  async function resolveSurveyPlace(fix) {
    if (navigator.onLine === false) {
      // No address offline: the position is the survey location for now (unless one was typed).
      if (!surveyLocation.trim() || autoFilled || isPlaceholder(surveyLocation)) {
        setSurveyLocation(placeholderFor(fix));
        setAutoFilled(true);
      }
      setGpsState("idle");
      setGpsMessage("Offline: the position is used as the survey location. It becomes the address when you're back online.");
      return;
    }
    setGpsState("resolving");
    try {
      const place = await reverseGeocode(fix.latitude, fix.longitude);
      setSurveyPlace(place || null);
      if (place?.label && (!surveyLocation.trim() || autoFilled)) {
        setSurveyLocation(place.label);
        setAutoFilled(true);
      }
      setGpsState("idle");
      setGpsMessage("");
    } catch (err) {
      setGpsState("idle"); // the position itself is kept
      setGpsMessage(err?.response?.data?.detail || "Couldn't find the address right now. Type the survey location.");
    }
  }

  async function captureLocation() {
    setGpsMessage("");
    setGpsState("locating");
    try {
      const fix = { ...(await captureFix()), offline: navigator.onLine === false };
      setCoords(fix);
      await resolveSurveyPlace(fix);
    } catch (err) {
      setGpsState("error");
      setGpsMessage(err.message);
    }
  }

  // The survey's GPS is captured on its own as soon as the respondent step opens (no button needed).
  const autoTried = useRef(false);
  useEffect(() => {
    if (step >= 1 && !coords && !locked && !autoTried.current && gpsState === "idle") {
      autoTried.current = true;
      captureLocation();
    }
  }); // eslint-disable-line react-hooks/exhaustive-deps

  // Back online with a position taken offline and no address yet: look it up now.
  useEffect(() => {
    const onOnline = () => {
      if (coords && (!surveyLocation.trim() || isPlaceholder(surveyLocation))) resolveSurveyPlace(coords);
      else setGpsMessage((m) => (m.startsWith("Offline:") ? "" : m));
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  });

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
                    <Select
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
                    </Select>
                  )}
                </Field>
                <Field label="Product" required>
                  {(id) => (
                    <Select
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
                    </Select>
                  )}
                </Field>
                {versions.length > 1 && (
                  <Field label="Version" required>
                    {(id) => (
                      <Select id={id} className="input" value={versionValue}
                        onChange={(e) => setVersionChoice(e.target.value)}>
                        {!autoVersion && <option value="">Choose the version tasted</option>}
                        {versions.map((v) => (
                          <option key={v.version} value={String(v.version)}>
                            Version {v.version} · Survey{v.rounds.length > 1 ? "s" : ""} {v.rounds.join(", ")}
                            {v.live ? " (collecting now)" : " (complete)"}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                )}
              </div>
            </fieldset>

            {categoryId && !catalogLoading && categoryProducts.length === 0 && (
              <p className="field-hint catalog-hint">No active products in this category yet.</p>
            )}

            {/* Only a problem is worth showing here; the chosen product and survey are in the fields above. */}
            {product && !product.survey_ready && (
              <p className="field-hint catalog-hint">{product.name} needs 5–10 survey questions before it can be surveyed.</p>
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
                      <Select id={id} className="input" value={respondent.gender}
                        onChange={(e) => setRespondent({ ...respondent, gender: e.target.value })}>
                        <option value="">Choose</option>
                        {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
                      </Select>
                    )}
                  </Field>
                  <Field label="Age">
                    {(id) => (
                      <input id={id} className="input" type="number" inputMode="numeric" min="1" max="120"
                        value={respondent.age} onChange={(e) => setRespondent({ ...respondent, age: e.target.value })} />
                    )}
                  </Field>
                  {myPlan?.my_target ? (
                    <p className={`ap-note${myPlan.my_done >= myPlan.my_target ? " is-full" : ""}`} role="note">
                      Assigned responses <strong>{myPlan.my_target}</strong> · Submitted <strong>{myPlan.my_done}</strong> ·
                      Remaining <strong>{Math.max(myPlan.my_target - myPlan.my_done, 0)}</strong>
                      {myPlan.my_done >= myPlan.my_target ? " · all done (ask your Admin if more are needed)" : ""}
                    </p>
                  ) : null}
                  {myCities.length > 0 && (
                    <p className="ap-note" role="note">
                      You survey in <strong>{myCities.join(", ")}</strong> only · action plan “{myPlan.plan_name}”
                    </p>
                  )}
                  <CustomerPlace respondent={respondent} setRespondent={setRespondent}
                    onLocated={(found, place) => {
                      // The survey usually happens where the customer is: fill the survey's GPS too, if empty.
                      if (!coords) setCoords(found);
                      // Offline (no address): the position stands in for the survey location.
                      const text = place?.label || (place ? "" : placeholderFor(found));
                      if (text && (!surveyLocation.trim() || autoFilled || isPlaceholder(surveyLocation))) {
                        setSurveyLocation(text);
                        setAutoFilled(true);
                      }
                    }} />
                  <Field label="Address (optional)">
                    {(id) => (
                      <input id={id} className="input" value={respondent.address}
                        onChange={(e) => setRespondent({ ...respondent, address: e.target.value })} />
                    )}
                  </Field>
                  {[["respondent_type", "Respondent type"], ["occupation", "Occupation"], ["sec_class", "Class (SEC)"]].map(([key, label]) => (
                    <Field key={key} label={label}>
                      {(id) => (
                        <Select id={id} className="input" value={respondent[key]} onChange={(e) => setRespondent({ ...respondent, [key]: e.target.value })}>
                          <option value="">Choose</option>
                          {(masters[key] || []).map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
                        </Select>
                      )}
                    </Field>
                  ))}
                </div>
              </fieldset>

              <fieldset className="form-section" disabled={locked}>
                <legend>This survey</legend>
                <div className="field">
                  <span className="field-label" id="audience-label">Internal / External</span>
                  <ChoiceGroup label="Internal / External" options={["External", "Internal"]} compact
                    value={audience === "internal" ? "Internal" : "External"} onChange={(v) => setAudience(v.toLowerCase())} />
                </div>
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
                      <input id={id} className="input" placeholder="e.g. D-Mart, Andheri West, or use GPS below" value={surveyLocation}
                        onChange={(e) => {
                          setAutoFilled(false); // typed by hand: GPS won't overwrite it
                          setSurveyLocation(e.target.value);
                        }} />
                    )}
                  </Field>
                  <div className="field is-wide">
                    <span className="field-label">
                      How long have you been {verb} {product?.name || "the product"}?<span className="req" aria-hidden="true"> *</span>
                    </span>
                    <ChoiceGroup label={`How long ${verb}`} options={USAGE_PERIODS} value={usagePeriod} onChange={setUsagePeriod} compact />
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
                        {(q.question_type === "single_choice" || q.question_type === "preference") && (
                          <ChoiceGroup label={q.text} options={Array.isArray(q.options) ? q.options : ["Sample A", "Sample B"]}
                            value={value} onChange={set} compact={q.question_type === "preference"} />
                        )}
                        {q.question_type === "multiple_choice" && (
                          <MultiChoice label={q.text} options={Array.isArray(q.options) ? q.options : []} value={value} onChange={set} />
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