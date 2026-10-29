/*
  Icons for the main navigation. They show only on phones, where the navigation
  becomes a bottom tab bar (see responsive.css); on desktop they are hidden.
*/
const PATHS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  survey: "M9 4h6a1 1 0 0 1 1 1v1h2a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h2V5a1 1 0 0 1 1-1zm0 2v1h6V6zM8 12h8M8 16h5",
  mine: "M4 6h16M4 12h16M4 18h10",
  history: "M12 7v5l3 2M21 12a9 9 0 1 1-3-6.7M21 4v5h-5",
  catalog: "M4 8 12 4l8 4v8l-8 4-8-4zm0 0 8 4 8-4M12 12v8",
  team: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-1a4 4 0 0 0-3-3.9M16 4.1a3.5 3.5 0 0 1 0 6.8",
  overview: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  products: "M4 8 12 4l8 4v8l-8 4-8-4zm0 0 8 4 8-4M12 12v8",
  updates: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
};

export default function NavIcon({ name }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}
