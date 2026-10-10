import { useCallback, useEffect, useMemo, useState } from "react";
import { confirmDialog } from "./ConfirmDialog";
import Modal from "./Modal";
import {
  catalogError,
  createCategory,
  createProduct,
  createQuestion,
  getProductExecutives,
  getRecipe,
  listTeam,
  saveProductExecutives,
  listCategories,
  listProducts,
  listQuestions,
  removeQuestion,
  reorderQuestions,
  saveRecipe,
  updateCategory,
  updateProduct,
  updateQuestion,
} from "../services/catalogAdminApi";

/*
  Catalog (admin)
    Overview: setup guide, category sidebar, products table
    Product page: Details · Survey questions · Recipe
  Everything saves to the database and reaches the web survey and the mobile app at once.
*/

const listOf = (d, key) => (Array.isArray(d) ? d : Array.isArray(d?.[key]) ? d[key] : []);

const TYPES = {
  rating: "Rating 1–5",
  yes_no: "Yes / No",
  single_choice: "Choice",
  text: "Text answer",
};

const MIN_Q = 5;
const MAX_Q = 10;

function Notice({ notice, onClose }) {
  if (!notice) return null;
  return (
    <div className={`catalog-notice is-${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
      <span>{notice.text}</span>
      <button type="button" className="icon-btn" aria-label="Dismiss" onClick={onClose}>×</button>
    </div>
  );
}

function productStatus(p) {
  if (!p.is_active) return { tone: "neutral", label: "Inactive" };
  if (p.question_count < MIN_Q) {
    const left = MIN_Q - p.question_count;
    return { tone: "warn", label: `Needs ${left} more ${left === 1 ? "question" : "questions"}` };
  }
  return { tone: "ok", label: "Ready for surveys" };
}

/* ---------------- Dialog ---------------- */

function CategoryDialog({ category, onClose, onSaved }) {
  const [name, setName] = useState(category?.name || "");
  const [description, setDescription] = useState(category?.description || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = name.trim().length >= 2;

  const save = async () => {
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const body = { name: name.trim(), description: description.trim() || null };
      const saved = category ? await updateCategory(category.id, body) : await createCategory(body);
      onSaved(saved, category ? "Category updated." : `Category "${saved.name}" created. Now add its products.`);
    } catch (err) {
      setError(catalogError(err, "The category couldn't be saved."));
      setBusy(false);
    }
  };

  return (
    <Modal
      title={category ? "Edit category" : "Add a category"}
      subtitle={category ? null : "Group similar products, e.g. Beverages, Personal Care, Snacks."}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={!valid || busy} onClick={save}>
            {busy ? "Saving…" : category ? "Save" : "Create category"}
          </button>
        </>
      }
    >
      <label className="field">
        <span className="field-label">Category name *</span>
        <input className="input" value={name} autoFocus placeholder="e.g. Beverages"
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea className="input" rows={2} value={description} placeholder="Optional"
          onChange={(e) => setDescription(e.target.value)} />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

function ProductDialog({ categories, defaultCategoryId, onClose, onCreated, onNeedCategory }) {
  const active = categories.filter((c) => c.is_active);
  const [form, setForm] = useState({
    category_id: defaultCategoryId || active[0]?.id || "",
    name: "",
    sku: "",
    description: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const valid = form.name.trim().length >= 2 && form.category_id;

  if (!active.length) {
    return (
      <Modal title="Add a product" onClose={onClose}
        footer={<button type="button" className="btn btn-primary" onClick={onNeedCategory}>Add a category first</button>}>
        <p>Products belong to a category. Create a category first, e.g. "Food & Confectionery".</p>
      </Modal>
    );
  }

  const save = async () => {
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const p = await createProduct({
        category_id: Number(form.category_id),
        name: form.name.trim(),
        sku: form.sku.trim() || null,
        description: form.description.trim() || null,
      });
      onCreated(p);
    } catch (err) {
      setError(catalogError(err, "The product couldn't be created."));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Add a product"
      subtitle="Next you'll add its survey questions and recipe."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={!valid || busy} onClick={save}>
            {busy ? "Creating…" : "Create product"}
          </button>
        </>
      }
    >
      <label className="field">
        <span className="field-label">Category *</span>
        <select className="input" value={form.category_id} onChange={set("category_id")}>
          {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Product name *</span>
        <input className="input" value={form.name} autoFocus placeholder="e.g. Aam (Mango) Jelly" onChange={set("name")} />
      </label>
      <label className="field">
        <span className="field-label">SKU</span>
        <input className="input" value={form.sku} placeholder="e.g. FOOD-AAM-JELLY" onChange={set("sku")} />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea className="input" rows={2} value={form.description} placeholder="Pack size, variant… (optional)" onChange={set("description")} />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/* ================================================================
   Page
   ================================================================ */

export default function CatalogPage() {
  const [categories, setCategories] = useState([]);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [categoryId, setCategoryId] = useState("all");
  const [productId, setProductId] = useState(null);
  const [startTab, setStartTab] = useState("details");
  const [query, setQuery] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [dialog, setDialog] = useState(null); // { type: "category" | "product", category? }
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([listCategories(true), listProducts(undefined, true)]);
      setCategories(listOf(c, "categories"));
      setProducts(listOf(p, "products"));
      setLoadError("");
    } catch (err) {
      setLoadError(catalogError(err, "The catalog couldn't be loaded."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const notify = useCallback((tone, text) => setNotice({ tone, text }), []);

  const category = categories.find((c) => c.id === categoryId) || null;
  const product = products.find((p) => p.id === productId) || null;

  const activeProducts = products.filter((p) => p.is_active);
  const readyCount = activeProducts.filter((p) => p.question_count >= MIN_Q && p.question_count <= MAX_Q).length;
  const recipeCount = activeProducts.filter((p) => p.has_recipe).length;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return products
      .filter((p) => categoryId === "all" || p.category_id === categoryId)
      .filter((p) => showInactive || p.is_active)
      .filter((p) => !q || `${p.name} ${p.sku || ""} ${p.category_name || ""}`.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [products, categoryId, showInactive, query]);

  const countIn = (cid) => products.filter((p) => p.category_id === cid && p.is_active).length;

  const openProduct = (id, tab = "details") => {
    setStartTab(tab);
    setProductId(id);
    window.scrollTo({ top: 0 });
  };

  const toggleCategory = async (c) => {
    if (c.is_active) {
      const ok = await confirmDialog({
        title: `Deactivate "${c.name}"?`,
        message: "Its products disappear from new surveys. Feedback already collected is kept, and you can reactivate it any time.",
        confirmLabel: "Deactivate",
        cancelLabel: "Keep",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await updateCategory(c.id, { is_active: !c.is_active });
      notify("ok", c.is_active ? `"${c.name}" deactivated.` : `"${c.name}" is active again.`);
      load();
    } catch (err) {
      notify("error", catalogError(err));
    }
  };

  // Setup guide: what's done and what's missing.
  const firstNeedingQuestions = activeProducts.find((p) => p.question_count < MIN_Q);
  const firstWithoutRecipe = activeProducts.find((p) => !p.has_recipe);
  const steps = [
    {
      done: categories.some((c) => c.is_active),
      title: "Categories",
      text: categories.length ? `${categories.filter((c) => c.is_active).length} active` : "Group your products",
      action: !categories.length ? { label: "Add category", run: () => setDialog({ type: "category" }) } : null,
    },
    {
      done: activeProducts.length > 0,
      title: "Products",
      text: activeProducts.length ? `${activeProducts.length} active` : "Add what you want feedback on",
      action: !activeProducts.length ? { label: "Add product", run: () => setDialog({ type: "product" }) } : null,
    },
    {
      done: activeProducts.length > 0 && readyCount === activeProducts.length,
      title: "Survey questions",
      text: activeProducts.length ? `${readyCount} of ${activeProducts.length} products ready` : "5–10 per product",
      action: firstNeedingQuestions ? { label: "Add questions", run: () => openProduct(firstNeedingQuestions.id, "questions") } : null,
    },
    {
      done: activeProducts.length > 0 && recipeCount === activeProducts.length,
      title: "Recipes",
      text: activeProducts.length ? `${recipeCount} of ${activeProducts.length} products have one` : "Powers recipe advice",
      action: firstWithoutRecipe ? { label: "Add a recipe", run: () => openProduct(firstWithoutRecipe.id, "recipe") } : null,
    },
  ];
  const setupDone = steps.every((s) => s.done);

  const dialogs = (
    <>
      {dialog?.type === "category" && (
        <CategoryDialog
          category={dialog.category}
          onClose={() => setDialog(null)}
          onSaved={(c, message) => {
            setDialog(null);
            notify("ok", message);
            setCategoryId(c.id);
            setProductId(null);
            load();
          }}
        />
      )}
      {dialog?.type === "product" && (
        <ProductDialog
          categories={categories}
          defaultCategoryId={categoryId !== "all" ? categoryId : undefined}
          onClose={() => setDialog(null)}
          onNeedCategory={() => setDialog({ type: "category" })}
          onCreated={async (p) => {
            setDialog(null);
            await load();
            notify("ok", `"${p.name}" created. Add 5–10 survey questions so executives can survey it.`);
            openProduct(p.id, "questions");
          }}
        />
      )}
    </>
  );

  /* ---------------- Product page ---------------- */
  if (product) {
    const status = productStatus(product);
    return (
      <section className="catalog" aria-labelledby="product-title">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <button type="button" className="link-btn" onClick={() => setProductId(null)}>Catalog</button>
          <span aria-hidden="true">›</span>
          <button type="button" className="link-btn" onClick={() => { setCategoryId(product.category_id); setProductId(null); }}>
            {product.category_name || "Category"}
          </button>
          <span aria-hidden="true">›</span>
          <span aria-current="page">{product.name}</span>
        </nav>
        <header className="home-head product-head">
          <div className="page-heading">
            <h1 id="product-title">{product.name}</h1>
            <span className={`status-chip is-${status.tone}`}>{status.label}</span>
            <span className={`status-chip ${product.has_recipe ? "is-ok" : "is-neutral"}`}>
              {product.has_recipe ? `Recipe · ${product.recipe_steps} steps` : "No recipe"}
            </span>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => setProductId(null)}>Back to catalog</button>
        </header>
        <Notice notice={notice} onClose={() => setNotice(null)} />
        <ProductPage key={product.id} product={product} categories={categories} startTab={startTab} onChanged={load} notify={notify} />
        {dialogs}
      </section>
    );
  }

  /* ---------------- Overview ---------------- */
  return (
    <section className="catalog" aria-labelledby="catalog-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="catalog-title">Catalog</h1>
          <p className="home-sub">The products executives survey, their questions and recipes.</p>
        </div>
        <div className="catalog-head-actions">
          <button type="button" className="btn btn-secondary" onClick={() => setDialog({ type: "category" })}>
            Add category
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ type: "product" })}>
            Add product
          </button>
        </div>
      </header>

      <Notice notice={notice} onClose={() => setNotice(null)} />
      {loadError && (
        <div className="catalog-notice is-error" role="alert">
          <span>{loadError}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={load}>Try again</button>
        </div>
      )}

      {!loading && (
        <section className={`setup-guide${setupDone ? " is-done" : ""}`} aria-label="Catalog setup">
          {steps.map((st, i) => (
            <div key={st.title} className={`setup-step${st.done ? " is-done" : ""}`}>
              <span className="setup-num" aria-hidden="true">{st.done ? "✓" : i + 1}</span>
              <div className="setup-text">
                <strong>{st.title}</strong>
                <span>{st.text}</span>
                {st.action && (
                  <button type="button" className="link-btn" onClick={st.action.run}>{st.action.label} →</button>
                )}
              </div>
            </div>
          ))}
        </section>
      )}

      <div className="catalog-layout">
        <aside className="panel catalog-sidebar" aria-label="Categories">
          <p className="sidebar-label">Categories</p>
          <ul className="sidebar-list">
            <li>
              <button type="button" className={categoryId === "all" ? "is-on" : ""} onClick={() => setCategoryId("all")}>
                <span>All products</span>
                <span className="sidebar-count">{activeProducts.length}</span>
              </button>
            </li>
            {categories
              .filter((c) => showInactive || c.is_active)
              .map((c) => (
                <li key={c.id}>
                  <button type="button" className={`${categoryId === c.id ? "is-on" : ""}${c.is_active ? "" : " is-inactive"}`}
                    onClick={() => setCategoryId(c.id)}>
                    <span>{c.name}</span>
                    <span className="sidebar-count">{c.is_active ? countIn(c.id) : "off"}</span>
                  </button>
                </li>
              ))}
          </ul>
          <button type="button" className="sidebar-add" onClick={() => setDialog({ type: "category" })}>+ Add category</button>
          <label className="sidebar-toggle">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
        </aside>

        <section className="panel catalog-main" aria-labelledby="products-title">
          <div className="catalog-main-head">
            <div>
              <h2 className="panel-title" id="products-title">{category ? category.name : "All products"}</h2>
              <p className="muted">
                {category?.description ||
                  (category ? `${countIn(category.id)} active products` : `${activeProducts.length} active products in ${categories.filter((c) => c.is_active).length} categories`)}
              </p>
            </div>
            {category && (
              <div className="catalog-main-actions">
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDialog({ type: "category", category })}>Edit</button>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => toggleCategory(category)}>
                  {category.is_active ? "Deactivate" : "Reactivate"}
                </button>
              </div>
            )}
          </div>

          <div className="table-toolbar">
            <input type="search" className="search" placeholder="Search products or SKUs" value={query}
              onChange={(e) => setQuery(e.target.value)} aria-label="Search products" />
          </div>

          {loading ? (
            <p className="muted table-empty">Loading…</p>
          ) : visible.length === 0 ? (
            <div className="catalog-empty">
              <strong>{query ? "No products match your search" : category ? `No products in ${category.name} yet` : "No products yet"}</strong>
              <p>{query ? "Try a different name or SKU." : "Add a product to start collecting feedback on it."}</p>
              {!query && (
                <button type="button" className="btn btn-primary" onClick={() => setDialog({ type: "product" })}>Add product</button>
              )}
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table catalog-table">
                <caption className="visually-hidden">Products</caption>
                <thead>
                  <tr>
                    <th scope="col">Product</th>
                    {categoryId === "all" && <th scope="col" className="col-category">Category</th>}
                    <th scope="col">Survey questions</th>
                    <th scope="col">Recipe</th>
                    <th scope="col">Status</th>
                    <th scope="col"><span className="visually-hidden">Open</span></th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((p) => {
                    const st = productStatus(p);
                    return (
                      <tr key={p.id} className={`is-clickable${p.is_active ? "" : " is-pending"}`} onClick={() => openProduct(p.id)}>
                        <td>
                          <div className="cell-product">
                            <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); openProduct(p.id); }}>
                              {p.name}
                            </button>
                            <span>{p.sku || "No SKU"}</span>
                          </div>
                        </td>
                        {categoryId === "all" && <td className="col-category">{p.category_name}</td>}
                        <td>
                          <div className="cell-progress">
                            <div className="mini-progress" aria-hidden="true">
                              <span style={{ width: `${Math.min(100, (p.question_count / MIN_Q) * 100)}%` }} />
                            </div>
                            <span className="cell-progress-text">{p.question_count}</span>
                          </div>
                        </td>
                        <td>
                          {p.has_recipe ? (
                            <span className="recipe-yes">✓ {p.recipe_steps} steps</span>
                          ) : (
                            <button type="button" className="link-btn" onClick={(e) => { e.stopPropagation(); openProduct(p.id, "recipe"); }}>
                              + Add recipe
                            </button>
                          )}
                        </td>
                        <td><span className={`status-chip is-${st.tone}`}>{st.label}</span></td>
                        <td className="cell-action"><span className="row-chevron" aria-hidden="true">›</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
      {dialogs}
    </section>
  );
}

/* ================================================================
   Product page: Details · Survey questions · Recipe
   ================================================================ */

function ProductPage({ product, categories, startTab, onChanged, notify }) {
  const [tab, setTab] = useState(startTab || "details");
  const onNotice = useCallback((n) => notify(n.tone, n.text), [notify]);
  const tabs = [
    ["details", "Product details"],
    ["recipe", product.has_recipe ? "Recipe" : "Recipe · not added"],
    ["questions", `Survey questions (${product.question_count})`],
    ["access", "Who can survey"],
  ];
  return (
    <>
      <div className="product-tabs" role="tablist" aria-label="Product">
        {tabs.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "is-on" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      <section className="panel product-panel">
        {tab === "details" && <DetailsTab product={product} categories={categories} onChanged={onChanged} notify={notify} />}
        {tab === "access" && <SurveyAccess product={product} notify={notify} />}
        {tab === "questions" && <QuestionsTab product={product} onChanged={onChanged} onNotice={onNotice} />}
        {tab === "recipe" && <RecipeTab product={product} onNotice={onNotice} onSaved={onChanged} />}
      </section>
    </>
  );
}

/* Who may survey this product: every executive, or only chosen ones. */
function SurveyAccess({ product, notify }) {
  const [state, setState] = useState({ loading: true, team: [], selected: new Set(), saved: "" });
  const [mode, setMode] = useState("all"); // all | selected
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([listTeam(), getProductExecutives(product.id)])
      .then(([team, assigned]) => {
        if (!alive) return;
        const people = listOf(team, "users").filter((u) => u.role !== "admin");
        const ids = assigned?.user_ids || [];
        setState({ loading: false, team: people, selected: new Set(ids), saved: JSON.stringify([...ids].sort()) });
        setMode(ids.length ? "selected" : "all");
      })
      .catch((err) => alive && setState((s) => ({ ...s, loading: false, error: catalogError(err) })));
    return () => {
      alive = false;
    };
  }, [product.id]);

  const toggle = (id) =>
    setState((s) => {
      const next = new Set(s.selected);
      next.has(id) ? next.delete(id) : next.add(id);
      return { ...s, selected: next };
    });

  const wanted = mode === "all" ? [] : [...state.selected].sort((a, b) => a - b);
  const dirty = JSON.stringify(wanted) !== state.saved;
  const save = async () => {
    if (mode === "selected" && !wanted.length) {
      notify("error", "Choose at least one executive, or switch to Every executive.");
      return;
    }
    setBusy(true);
    try {
      const res = await saveProductExecutives(product.id, wanted);
      const ids = res?.user_ids || [];
      setState((s) => ({ ...s, saved: JSON.stringify([...ids].sort((a, b) => a - b)) }));
      notify("ok", ids.length ? `${product.name} is now surveyed only by the ${ids.length} chosen ${ids.length === 1 ? "executive" : "executives"}.` : `${product.name} is open to every executive.`);
    } catch (err) {
      notify("error", catalogError(err, "Couldn't save who surveys this product."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="survey-access" aria-labelledby="access-title">
      <div className="tab-intro">
        <strong id="access-title">Who can survey this product</strong>
        <p className="muted">Decide which survey executives see {product.name} in their product list, on the web and in the app.</p>
      </div>
      {state.loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="access-step">
            <h4 className="access-step-title"><span className="access-step-no">1</span>Who should see this product?</h4>
            <div className="access-choice" role="radiogroup" aria-label="Who can survey this product">
              {[
                ["all", "👥", "Every executive", "The whole team can survey it"],
                ["selected", "🔒", "Only the executives I choose", "Hidden from everyone else"],
              ].map(([key, icon, title, hint]) => (
                <button key={key} type="button" role="radio" aria-checked={mode === key}
                  className={`access-option${mode === key ? " is-on" : ""}`} onClick={() => setMode(key)}>
                  <span className="access-option-icon" aria-hidden="true">{icon}</span>
                  <span className="access-option-text">
                    <strong>{title}</strong>
                    <span>{hint}</span>
                  </span>
                  <span className="access-option-dot" aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>

          {mode === "selected" && (
            <div className="access-step">
              <div className="access-people-head">
                <h4 className="access-step-title"><span className="access-step-no">2</span>Choose the executives</h4>
                {state.team.length > 0 && (
                  <span className="access-people-tools">
                    <span className="muted">{state.selected.size} of {state.team.length} selected</span>
                    <button type="button" className="link-btn"
                      onClick={() => setState((x) => ({ ...x, selected: new Set(x.team.map((u) => u.id)) }))}>Select all</button>
                    <button type="button" className="link-btn" onClick={() => setState((x) => ({ ...x, selected: new Set() }))}>Clear</button>
                  </span>
                )}
              </div>
              {state.team.length === 0 ? (
                <p className="muted">No executives yet. Add them on the Team page.</p>
              ) : (
                <ul className="people-list">
                  {state.team.map((u) => {
                    const on = state.selected.has(u.id);
                    return (
                      <li key={u.id} className={on ? "is-on" : ""}>
                        <span className="people-avatar" aria-hidden="true">{(u.name || "?").trim()[0]?.toUpperCase()}</span>
                        <span className="people-text">
                          <strong>{u.name}</strong>
                          <span className="muted">{u.mobile || u.email}{u.is_active ? "" : " · inactive"}</span>
                        </span>
                        <span className={`people-state${on ? " is-on" : ""}`}>{on ? "Can survey" : "Hidden"}</span>
                        <label className="toggle" aria-label={`${u.name} can survey ${product.name}`}>
                          <input type="checkbox" role="switch" checked={on} onChange={() => toggle(u.id)} />
                          <span className="toggle-track" aria-hidden="true"><span className="toggle-thumb" /></span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}

          <div className="access-result">
            <span className="access-result-text">
              {mode === "all"
                ? <>Every executive will see <strong>{product.name}</strong>.</>
                : state.selected.size === 0
                ? <>Choose at least one executive, or switch to <strong>Every executive</strong>.</>
                : <>Only <strong>{state.team.filter((u) => state.selected.has(u.id)).map((u) => u.name).join(", ")}</strong> will see {product.name}.</>}
            </span>
            <span className="access-result-actions">
              {dirty && <span className="muted">Unsaved changes</span>}
              <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
                {busy ? "Saving…" : "Save"}
              </button>
            </span>
          </div>
        </>
      )}
    </section>
  );
}

function DetailsTab({ product, categories, onChanged, notify }) {
  const [form, setForm] = useState({
    category_id: product.category_id,
    name: product.name || "",
    sku: product.sku || "",
    description: product.description || "",
  });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const save = async () => {
    setBusy(true);
    try {
      await updateProduct(product.id, {
        category_id: Number(form.category_id),
        name: form.name.trim(),
        sku: form.sku.trim() || null,
        description: form.description.trim() || null,
      });
      notify("ok", "Product saved.");
      onChanged();
    } catch (err) {
      notify("error", catalogError(err, "The product couldn't be saved."));
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async () => {
    if (product.is_active) {
      const ok = await confirmDialog({
        title: `Deactivate "${product.name}"?`,
        message: "It disappears from new surveys. Feedback already collected and its reports are kept.",
        confirmLabel: "Deactivate",
        cancelLabel: "Keep",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await updateProduct(product.id, { is_active: !product.is_active });
      notify("ok", product.is_active ? "Product deactivated." : "Product is active again.");
      onChanged();
    } catch (err) {
      notify("error", catalogError(err));
    }
  };

  return (
    <div className="catalog-fields details-form">
      <div className="catalog-row">
        <label className="field">
          <span className="field-label">Product name *</span>
          <input className="input" value={form.name} onChange={set("name")} />
        </label>
        <label className="field">
          <span className="field-label">Category</span>
          <select className="input" value={form.category_id} onChange={set("category_id")}>
            {categories.filter((c) => c.is_active || c.id === product.category_id).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span className="field-label">SKU</span>
        <input className="input" value={form.sku} onChange={set("sku")} placeholder="e.g. FOOD-AAM-JELLY" />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea className="input" rows={3} value={form.description} onChange={set("description")} placeholder="Pack size, variant, target customers…" />
      </label>
      <div className="details-actions">
        <button type="button" className="btn btn-secondary danger-outline" onClick={toggleActive}>
          {product.is_active ? "Deactivate product" : "Reactivate product"}
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || form.name.trim().length < 2} onClick={save}>
          {busy ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

/* ---------------- Questions ---------------- */

const emptyQuestion = { text: "", question_type: "rating", options: "", required: true, report_role: "" };

// How a question feeds the client summary of the consolidated report.
const REPORT_ROLES = {
  preference: { label: "Product preference", hint: "This product vs the alternative. Put this product as the FIRST option.", types: ["single_choice", "yes_no"] },
  price: { label: "Price willingness", hint: "Price points as options, e.g. 3 ghc, 4 ghc, 5 ghc.", types: ["single_choice"] },
  score: { label: "Overall score", hint: "1–5 rating, or options from lowest to highest, e.g. 7 marks … 10 marks.", types: ["rating", "single_choice"] },
  improvement: { label: "Improvement areas", hint: "What customers would change.", types: ["single_choice", "text"] },
  purchase_intent: { label: "Purchase intent", hint: "Would they buy it? Put the positive answer first.", types: ["yes_no", "single_choice"] },
  ease: { label: "Ease of use / preparation", hint: "How easy it was to use or prepare.", types: ["rating", "yes_no", "single_choice"] },
};

function QuestionsTab({ product, onChanged, onNotice }) {
  const [questions, setQuestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null); // { id|null, ...form }
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setQuestions(listOf(await listQuestions(product.id), "questions"));
    } catch (err) {
      onNotice({ tone: "error", text: catalogError(err, "Questions couldn't be loaded.") });
    } finally {
      setLoading(false);
    }
  }, [product.id, onNotice]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    setBusy(true);
    const body = {
      text: editing.text.trim(),
      question_type: editing.question_type,
      required: editing.required,
      report_role: editing.report_role || null,
      options:
        editing.question_type === "single_choice"
          ? editing.options.split("\n").map((o) => o.trim()).filter(Boolean)
          : null,
    };
    try {
      if (editing.id) await updateQuestion(editing.id, body);
      else await createQuestion(product.id, body);
      setEditing(null);
      await load();
      onChanged();
      onNotice({ tone: "ok", text: editing.id ? "Question updated." : "Question added." });
    } catch (err) {
      onNotice({ tone: "error", text: catalogError(err, "The question couldn't be saved.") });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (q) => {
    const ok = await confirmDialog({
      title: "Remove this question?",
      message: `"${q.text}" won't be asked in new surveys. Answers already collected are kept.`,
      confirmLabel: "Remove",
      cancelLabel: "Keep",
      danger: true,
    });
    if (!ok) return;
    try {
      await removeQuestion(q.id);
      await load();
      onChanged();
    } catch (err) {
      onNotice({ tone: "error", text: catalogError(err) });
    }
  };

  const move = async (index, dir) => {
    const ids = questions.map((q) => q.id);
    const j = index + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    setQuestions((qs) => ids.map((id) => qs.find((q) => q.id === id)));
    try {
      await reorderQuestions(product.id, ids);
    } catch (err) {
      onNotice({ tone: "error", text: catalogError(err) });
      load();
    }
  };

  const count = questions.length;
  const editor = editing && (
    <div className="catalog-form question-form">
      <label className="field">
        <span className="field-label">Question *</span>
        <input className="input" value={editing.text} autoFocus placeholder="e.g. How would you rate the mango taste?"
          onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
      </label>
      <div className="catalog-row">
        <label className="field">
          <span className="field-label">Answer type</span>
          <select className="input" value={editing.question_type} onChange={(e) => setEditing({ ...editing, question_type: e.target.value })}>
            {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="catalog-check">
          <input type="checkbox" checked={editing.required} onChange={(e) => setEditing({ ...editing, required: e.target.checked })} />
          Required
        </label>
      </div>
      <label className="field">
        <span className="field-label">Use in the client report as</span>
        <select className="input" value={editing.report_role || ""}
          onChange={(e) => setEditing({ ...editing, report_role: e.target.value })}>
          <option value="">Not in the client summary (still in survey answers)</option>
          {Object.entries(REPORT_ROLES)
            .filter(([k, r]) => r.types.includes(editing.question_type) || k === editing.report_role)
            .map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
        </select>
        {editing.report_role && REPORT_ROLES[editing.report_role] && (
          <span className="field-hint">
            {REPORT_ROLES[editing.report_role].types.includes(editing.question_type)
              ? REPORT_ROLES[editing.report_role].hint
              : `${REPORT_ROLES[editing.report_role].label} needs a ${REPORT_ROLES[editing.report_role].types.map((t) => TYPES[t]).join(" or ")} question.`}
          </span>
        )}
      </label>
      {editing.question_type === "single_choice" && (
        <label className="field">
          <span className="field-label">Options (2–8, one per line)</span>
          <textarea className="input" rows={4} value={editing.options} placeholder={"Too sweet\nJust right\nNot sweet enough"}
            onChange={(e) => setEditing({ ...editing, options: e.target.value })} />
        </label>
      )}
      <div className="catalog-form-actions">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEditing(null)}>Cancel</button>
        <button type="button" className="btn btn-primary btn-sm" disabled={busy || editing.text.trim().length < 5} onClick={save}>
          {busy ? "Saving…" : editing.id ? "Save question" : "Add question"}
        </button>
      </div>
    </div>
  );

  return (
    <div>
      <div className="tab-head">
        <div className="tab-intro">
          <strong>Survey questions</strong>
          <p className="muted">What executives ask each customer about {product.name}, in this order. Products need 5–10 questions.</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={count >= 10 || Boolean(editing)}
          title={count >= 10 ? "A product can have up to 10 questions" : undefined}
          onClick={() => setEditing({ id: null, ...emptyQuestion })}
        >
          + Add question
        </button>
      </div>
      {editing && !editing.id && editor}
      <p className={`catalog-hint ${count >= 5 && count <= 10 ? "is-ok" : "is-warn"}`}>
        {count} of 5–10 questions. {count < 5 ? `Add ${5 - count} more before it can be surveyed.` : "Ready for surveys."}
      </p>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <ol className="question-admin-list">
          {questions.map((q, i) =>
            editing?.id === q.id ? (
              <li key={q.id}>{editor}</li>
            ) : (
              <li key={q.id}>
                <span className="question-num">{i + 1}</span>
                <div className="question-admin-text">
                  <strong>{q.text}</strong>
                  <span className="question-meta">
                    <span className={`qtype qtype-${q.question_type}`}>{TYPES[q.question_type] || q.question_type}</span>
                    {!q.required && <span className="qtag">Optional</span>}
                    {q.report_role && REPORT_ROLES[q.report_role] && (
                      <span className="qtag is-report">Report: {REPORT_ROLES[q.report_role].label}</span>
                    )}
                    {q.options?.length ? <span className="muted qoptions">{q.options.join(" / ")}</span> : null}
                  </span>
                </div>
                <div className="question-admin-actions">
                  <button type="button" className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                  <button type="button" className="icon-btn" aria-label="Move down" disabled={i === count - 1} onClick={() => move(i, 1)}>↓</button>
                  <button type="button" className="link-btn"
                    onClick={() => setEditing({ id: q.id, text: q.text, question_type: q.question_type, required: q.required, options: (q.options || []).join("\n"), report_role: q.report_role || "" })}>
                    Edit
                  </button>
                  <button type="button" className="link-btn danger-link" onClick={() => remove(q)}>Remove</button>
                </div>
              </li>
            )
          )}
        </ol>
      )}

    </div>
  );
}

/* ---------------- Recipe ---------------- */

const blankIngredient = () => ({ name: "", quantity: "", unit: "", notes: "" });
const blankStep = () => ({ title: "", details: "", temperature: "", duration: "" });

// "82" + "kg" -> grams (mass) or millilitres (volume); null when it can't be compared.
const UNIT_FACTORS = { mg: ["mass", 0.001], g: ["mass", 1], gm: ["mass", 1], gram: ["mass", 1], grams: ["mass", 1], kg: ["mass", 1000],
  ml: ["vol", 1], l: ["vol", 1000], litre: ["vol", 1000], liter: ["vol", 1000], ltr: ["vol", 1000] };
function amountOf(i) {
  const q = parseFloat(String(i.quantity || "").replace(",", "."));
  const u = UNIT_FACTORS[String(i.unit || "").trim().toLowerCase()];
  return Number.isFinite(q) && u ? { kind: u[0], value: q * u[1] } : null;
}

// "30 min", "1 h", "1.5 hr", "45 sec" -> minutes; null when unclear.
function minutesOf(text) {
  const m = String(text || "").toLowerCase().match(/([\d.]+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b/);
  if (!m) return null;
  const v = parseFloat(m[1]);
  if (!Number.isFinite(v)) return null;
  return m[2].startsWith("h") ? v * 60 : m[2].startsWith("s") ? v / 60 : v;
}
const fmtMinutes = (m) => (m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${Math.round(m % 60)} min` : ""}` : `${Math.round(m)} min`);

function RecipeTab({ product, onNotice, onSaved }) {
  const [recipe, setRecipe] = useState(null);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState({ batch: false, ingredients: false, steps: false });

  useEffect(() => {
    let alive = true;
    getRecipe(product.id)
      .then((d) => {
        if (!alive) return;
        const r = d?.recipe || {};
        const loaded = {
          batch_size: r.batch_size || "",
          ingredients: (r.ingredients || []).map((i) => ({ ...blankIngredient(), ...i })),
          steps: (r.steps || []).map((x) => ({ ...blankStep(), ...x })),
          notes: r.notes || "",
        };
        setRecipe(loaded);
        setSaved(JSON.stringify(loaded));
      })
      .catch((err) => onNotice({ tone: "error", text: catalogError(err, "The recipe couldn't be loaded.") }));
    return () => {
      alive = false;
    };
  }, [product.id, onNotice]);

  const dirty = useMemo(() => recipe && JSON.stringify(recipe) !== saved, [recipe, saved]);
  if (!recipe) return <p className="muted">Loading recipe…</p>;

  const setList = (key, i, field, value) =>
    setRecipe((r) => ({ ...r, [key]: r[key].map((row, k) => (k === i ? { ...row, [field]: value } : row)) }));
  const addRow = (key) => setRecipe((r) => ({ ...r, [key]: [...r[key], key === "steps" ? blankStep() : blankIngredient()] }));
  const removeRow = (key, i) => setRecipe((r) => ({ ...r, [key]: r[key].filter((_, k) => k !== i) }));
  const moveStep = (i, dir) =>
    setRecipe((r) => {
      const steps = [...r.steps];
      const j = i + dir;
      if (j < 0 || j >= steps.length) return r;
      [steps[i], steps[j]] = [steps[j], steps[i]];
      return { ...r, steps };
    });
  const edit = (part, on = true) => {
    setEditing((e) => ({ ...e, [part]: on }));
    // Start an empty list with one row to fill in.
    if (on && (part === "ingredients" || part === "steps") && !recipe[part].length) addRow(part);
  };

  const save = async () => {
    setBusy(true);
    try {
      const clean = {
        batch_size: recipe.batch_size.trim() || null,
        ingredients: recipe.ingredients.filter((i) => i.name.trim()),
        steps: recipe.steps.filter((x) => x.title.trim()),
        notes: recipe.notes.trim() || null,
      };
      const res = await saveRecipe(product.id, clean);
      const next = { ...recipe, ingredients: clean.ingredients, steps: clean.steps };
      setRecipe(next);
      setSaved(JSON.stringify(next));
      setEditing({ batch: false, ingredients: false, steps: false });
      onNotice({
        tone: "ok",
        text: `Recipe saved: ${res?.recipe?.ingredients?.length || 0} ingredients, ${res?.recipe?.steps?.length || 0} steps. The next report refresh uses it.`,
      });
      onSaved?.();
    } catch (err) {
      onNotice({ tone: "error", text: catalogError(err, "The recipe couldn't be saved.") });
    } finally {
      setBusy(false);
    }
  };
  const discard = () => {
    setRecipe(JSON.parse(saved));
    setEditing({ batch: false, ingredients: false, steps: false });
  };

  // Share of the batch, when every quantity is a weight (or every one a volume).
  const amounts = recipe.ingredients.map(amountOf);
  const kinds = new Set(amounts.filter(Boolean).map((a) => a.kind));
  const comparable = amounts.length > 0 && amounts.every(Boolean) && kinds.size === 1;
  const total = comparable ? amounts.reduce((n, a) => n + a.value, 0) : 0;
  const totalLabel = comparable ? (kinds.has("mass") ? (total >= 1000 ? `${+(total / 1000).toFixed(2)} kg` : `${+total.toFixed(1)} g`) : (total >= 1000 ? `${+(total / 1000).toFixed(2)} l` : `${+total.toFixed(1)} ml`)) : null;
  const stepMinutes = recipe.steps.map((x) => minutesOf(x.duration));
  const totalMinutes = recipe.steps.length && stepMinutes.every((m) => m != null) ? stepMinutes.reduce((a, b) => a + b, 0) : null;

  return (
    <div className="recipe">
      <div className="tab-intro">
        <strong>Recipe</strong>
        <p className="muted">
          How {product.name} is made, from raw materials to the packed product. Consolidated reports use it to link customer
          feedback to an exact ingredient or step, e.g. "7 of 10 find it too sweet: reduce sugar in step 1".
        </p>
      </div>

      {/* ---- batch ---- */}
      <section className="recipe-card">
        <header className="recipe-card-head">
          <div>
            <h3>Batch size</h3>
            <p className="muted">How much one production batch makes. The ingredient quantities below are for this batch.</p>
          </div>
          {!editing.batch && <button type="button" className="btn btn-secondary btn-sm" onClick={() => edit("batch")}>Edit</button>}
        </header>
        {editing.batch ? (
          <label className="field recipe-field">
            <span className="field-label">Batch size</span>
            <input className="input" value={recipe.batch_size} autoFocus placeholder="e.g. 100 kg batch = 200 packs of 500 g"
              onChange={(e) => setRecipe({ ...recipe, batch_size: e.target.value })} />
          </label>
        ) : (
          <p className={recipe.batch_size ? "recipe-value" : "muted"}>{recipe.batch_size || "Not set yet."}</p>
        )}
      </section>

      {/* ---- ingredients ---- */}
      <section className="recipe-card">
        <header className="recipe-card-head">
          <div>
            <h3>Ingredients for one batch <span className="count-badge">{recipe.ingredients.filter((i) => i.name.trim()).length}</span></h3>
            <p className="muted">What goes into the product, how much, and why it's there.</p>
          </div>
          {!editing.ingredients && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => edit("ingredients")}>
              {recipe.ingredients.length ? "Edit ingredients" : "Add ingredients"}
            </button>
          )}
        </header>

        {editing.ingredients ? (
          <div className="ing-edit">
            <div className="ing-edit-head" aria-hidden="true">
              <span>Ingredient</span><span>Quantity</span><span>Unit</span><span>Purpose in the recipe</span><span />
            </div>
            {recipe.ingredients.map((ing, i) => (
              <div className="ing-edit-row" key={i}>
                <input className="input" aria-label={`Ingredient ${i + 1}`} placeholder="e.g. Sugar" value={ing.name}
                  onChange={(e) => setList("ingredients", i, "name", e.target.value)} />
                <input className="input" aria-label="Quantity" inputMode="decimal" placeholder="8" value={ing.quantity}
                  onChange={(e) => setList("ingredients", i, "quantity", e.target.value)} />
                <input className="input" aria-label="Unit" placeholder="kg" value={ing.unit}
                  onChange={(e) => setList("ingredients", i, "unit", e.target.value)} />
                <input className="input" aria-label="Purpose" placeholder="e.g. Sweetness" value={ing.notes}
                  onChange={(e) => setList("ingredients", i, "notes", e.target.value)} />
                <button type="button" className="icon-btn danger" aria-label={`Remove ingredient ${i + 1}`} onClick={() => removeRow("ingredients", i)}>×</button>
              </div>
            ))}
            <div className="recipe-edit-foot">
              <button type="button" className="link-btn" onClick={() => addRow("ingredients")}>+ Add ingredient</button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => edit("ingredients", false)}>Done</button>
            </div>
          </div>
        ) : recipe.ingredients.length ? (
          <div className="table-wrap">
            <table className="data-table ing-table">
              <thead>
                <tr>
                  <th scope="col">Ingredient</th>
                  <th scope="col" className="num-cell">Quantity</th>
                  {comparable && <th scope="col">Share of batch</th>}
                  <th scope="col">Purpose</th>
                </tr>
              </thead>
              <tbody>
                {recipe.ingredients.map((ing, i) => {
                  const share = comparable && total ? (100 * amounts[i].value) / total : null;
                  return (
                    <tr key={i}>
                      <td><strong>{ing.name}</strong></td>
                      <td className="num-cell">{[ing.quantity, ing.unit].filter(Boolean).join(" ") || "—"}</td>
                      {comparable && (
                        <td>
                          <span className="share">
                            <span className="share-track"><span style={{ width: `${Math.max(2, share)}%` }} /></span>
                            <span className="share-val">{share < 1 ? share.toFixed(1) : Math.round(share)}%</span>
                          </span>
                        </td>
                      )}
                      <td className="muted">{ing.notes || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
              {comparable && (
                <tfoot>
                  <tr>
                    <td><strong>Total</strong></td>
                    <td className="num-cell"><strong>{totalLabel}</strong></td>
                    <td><strong>100%</strong></td>
                    <td />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        ) : (
          <p className="recipe-empty">No ingredients yet. Add them so reports can point to an exact ingredient.</p>
        )}
      </section>

      {/* ---- production process ---- */}
      <section className="recipe-card">
        <header className="recipe-card-head">
          <div>
            <h3>Production process <span className="count-badge">{recipe.steps.filter((x) => x.title.trim()).length} steps</span></h3>
            <p className="muted">
              How {product.name} is made, in order{totalMinutes != null ? ` · about ${fmtMinutes(totalMinutes)} in total` : ""}.
            </p>
          </div>
          {!editing.steps && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => edit("steps")}>
              {recipe.steps.length ? "Edit steps" : "Add steps"}
            </button>
          )}
        </header>

        {editing.steps ? (
          <div className="step-edit">
            {recipe.steps.map((st, i) => (
              <div className="step-edit-card" key={i}>
                <div className="step-edit-top">
                  <span className="step-no">Step {i + 1}</span>
                  <span className="step-edit-tools">
                    <button type="button" className="icon-btn" aria-label="Move step up" disabled={i === 0} onClick={() => moveStep(i, -1)}>↑</button>
                    <button type="button" className="icon-btn" aria-label="Move step down" disabled={i === recipe.steps.length - 1} onClick={() => moveStep(i, 1)}>↓</button>
                    <button type="button" className="icon-btn danger" aria-label={`Remove step ${i + 1}`} onClick={() => removeRow("steps", i)}>×</button>
                  </span>
                </div>
                <div className="step-edit-grid">
                  <label className="field step-name">
                    <span className="field-label">Step name</span>
                    <input className="input" placeholder="e.g. Cook the corn grits" value={st.title}
                      onChange={(e) => setList("steps", i, "title", e.target.value)} />
                  </label>
                  <label className="field">
                    <span className="field-label">Temperature</span>
                    <input className="input" placeholder="e.g. 85 °C" value={st.temperature}
                      onChange={(e) => setList("steps", i, "temperature", e.target.value)} />
                  </label>
                  <label className="field">
                    <span className="field-label">Time</span>
                    <input className="input" placeholder="e.g. 30 min" value={st.duration}
                      onChange={(e) => setList("steps", i, "duration", e.target.value)} />
                  </label>
                  <label className="field step-instructions">
                    <span className="field-label">Instructions</span>
                    <textarea className="input" rows={2} placeholder="What the operator does in this step"
                      value={st.details} onChange={(e) => setList("steps", i, "details", e.target.value)} />
                  </label>
                </div>
              </div>
            ))}
            <div className="recipe-edit-foot">
              <button type="button" className="link-btn" onClick={() => addRow("steps")}>+ Add step</button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => edit("steps", false)}>Done</button>
            </div>
          </div>
        ) : recipe.steps.length ? (
          <ol className="process">
            {recipe.steps.map((st, i) => (
              <li key={i}>
                <span className="process-no" aria-hidden="true">{i + 1}</span>
                <div className="process-body">
                  <div className="process-title">
                    <strong>{st.title || <span className="muted">Untitled step</span>}</strong>
                    <span className="process-tags">
                      {st.temperature && <span className="ptag">🌡 {st.temperature}</span>}
                      {st.duration && <span className="ptag">⏱ {st.duration}</span>}
                    </span>
                  </div>
                  {st.details && <p className="process-details">{st.details}</p>}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="recipe-empty">No steps yet. Add them in order, from raw materials to packing.</p>
        )}
      </section>

      {/* ---- notes ---- */}
      <section className="recipe-card">
        <header className="recipe-card-head">
          <div>
            <h3>Notes</h3>
            <p className="muted">Shelf life, storage, allergens, quality checks…</p>
          </div>
        </header>
        <textarea className="input" rows={2} value={recipe.notes} placeholder="Optional"
          onChange={(e) => setRecipe({ ...recipe, notes: e.target.value })} />
      </section>

      {dirty && (
        <div className="recipe-savebar" role="status">
          <span>You have unsaved changes to the recipe.</span>
          <span className="recipe-savebar-actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={discard} disabled={busy}>Discard</button>
            <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy}>
              {busy ? "Saving…" : "Save recipe"}
            </button>
          </span>
        </div>
      )}
    </div>
  );
}