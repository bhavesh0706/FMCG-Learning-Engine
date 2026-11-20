/*
  Icons for the KPI cards (dashboard numbers and the catalog's setup cards).
  Line icons on a 24px grid, drawn in the card's accent colour (App.css: .kpi-card).
*/
const PATHS = {
  // a box: products
  products: "M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5zM3.5 7.5 12 12l8.5-4.5M12 12v9M7.8 5.3l8.4 4.5",
  // a document with a check: analysed feedback
  analyzed: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 14.5l2 2 4-4.5",
  // a target: products surveyed (coverage)
  coverage: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  // speech bubbles: consumer responses
  responses: "M21 12a7.5 7.5 0 0 1-11 6.6L4 20l1.4-5A7.5 7.5 0 1 1 21 12zM9 11h.01M12.5 11h.01M16 11h.01",
  // a live signal: surveys collecting now
  live: "M12 13.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8",
  // folders: categories
  categories: "M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 10h18",
  // a checklist: survey questions
  questions: "M10 6h10M10 12h10M10 18h10M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  // a flask: recipes
  recipes: "M9 3h6M10 3v6.5L4.8 18.4A1.7 1.7 0 0 0 6.3 21h11.4a1.7 1.7 0 0 0 1.5-2.6L14 9.5V3M7.5 15h9",
  // an hourglass: drafts, survey not started yet
  draft: "M6 3h12M6 21h12M7 3v3.5a5 5 0 0 0 2.5 4.3L12 12l2.5-1.2A5 5 0 0 0 17 6.5V3M7 21v-3.5a5 5 0 0 1 2.5-4.3L12 12l2.5 1.2a5 5 0 0 1 2.5 4.3V21",
  // a calendar: today
  today: "M7 3v3M17 3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM12 12v4l2.5 1.5",
  // a pie: sentiment mix
  sentiment: "M12 3a9 9 0 1 0 9 9h-9zM15 3.5A9 9 0 0 1 20.5 9H15z",
  // a star: rating
  rating: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z",
};

export default function KpiIcon({ name }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <span className="kpi-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="22" height="22" focusable="false" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    </span>
  );
}
