/* Small line icons that give each page section its own identity (recipe sections, standards,
   survey runs, recording log, compare). Coloured by the section's tone in App.css (.sec-icon). */
const PATHS = {
  batch: "M4 7h16l-1.5 12a2 2 0 0 1-2 1.8H7.5a2 2 0 0 1-2-1.8zM8 7V5a4 4 0 0 1 8 0v2",
  ingredients: "M9 3h6M10 3v6.5L4.8 18.4A1.7 1.7 0 0 0 6.3 21h11.4a1.7 1.7 0 0 0 1.5-2.6L14 9.5V3M7.5 15h9",
  process: "M4 6h4v4H4zM16 6h4v4h-4zM10 16h4v4h-4zM8 8h8M18 10v3a2 2 0 0 1-2 2h-2M6 10v3a2 2 0 0 0 2 2h2",
  attach: "M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9",
  notes: "M6 3h9l5 5v13H6zM14 3v6h6M9 13h8M9 17h6",
  history: "M12 7v5l3 2M21 12a9 9 0 1 1-3-6.7M21 4v5h-5",
  costing: "M6 4h12M6 9h12M9 4c4 0 6 2 6 5s-2 5-6 5l7 6",
  standards: "M12 3l7 3v6c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6zM9 12l2 2 4-4",
  rules: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  play: "M8 5v14l11-7z",
  compare: "M8 3v18M16 3v18M3 8h5M16 16h5M3 16h5M16 8h5",
  people: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-1a4 4 0 0 0-3-3.9M16 4.1a3.5 3.5 0 0 1 0 6.8",
};

export default function SectionIcon({ name }) {
  return (
    <span className={`sec-icon sec-${name}`} aria-hidden="true">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d={PATHS[name] || PATHS.notes} />
      </svg>
    </span>
  );
}
