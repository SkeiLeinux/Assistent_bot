// icons.jsx — small inline SVG icons (24px viewBox)
const Icon = ({ d, size = 22, stroke = 'currentColor', fill = 'none', sw = 2 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);

const IconPlus      = (p) => <Icon {...p} d="M12 5v14M5 12h14" />;
const IconChevL     = (p) => <Icon {...p} d="M15 18l-6-6 6-6" />;
const IconChevR     = (p) => <Icon {...p} d="M9 6l6 6-6 6" />;
const IconChevDown  = (p) => <Icon {...p} d="M6 9l6 6 6-6" />;
const IconBack      = (p) => <Icon {...p} d="M19 12H5M12 19l-7-7 7-7" />;
const IconClose     = (p) => <Icon {...p} d="M18 6L6 18M6 6l12 12" />;
const IconCopy      = (p) => <Icon {...p} sw={1.7} d="M9 9h10v10H9zM5 5h10v4M5 5v10h4" />;
const IconCheck     = (p) => <Icon {...p} d="M5 13l4 4L19 7" />;
const IconText      = (p) => <Icon {...p} sw={1.8} d="M4 6h16M4 12h10M4 18h16" />;
const IconCamera    = (p) => (
  <svg width={p.size||22} height={p.size||22} viewBox="0 0 24 24" fill="none" stroke={p.stroke||'currentColor'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
  </svg>
);
const IconWarn      = (p) => <Icon {...p} sw={1.8} d="M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />;
const IconError     = (p) => <Icon {...p} sw={1.8} d="M12 8v4M12 16h.01" />;
const IconFile      = (p) => <Icon {...p} sw={1.8} d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />;
const IconShare     = (p) => <Icon {...p} sw={1.8} d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13" />;
const IconCalendar  = (p) => <Icon {...p} sw={1.8} d="M19 4H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zM16 2v4M8 2v4M3 10h18" />;
const IconTrash     = (p) => <Icon {...p} sw={1.8} d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6" />;
const IconEdit      = (p) => <Icon {...p} sw={1.8} d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />;
const IconRetry     = (p) => <Icon {...p} sw={1.8} d="M1 4v6h6M23 20v-6h-6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
const IconRefresh   = IconRetry;
const IconImage     = (p) => <Icon {...p} sw={1.8} d="M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zM8.5 11a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21" />;
const IconGear      = (p) => (
  <svg width={p.size||22} height={p.size||22} viewBox="0 0 24 24" fill="none" stroke={p.stroke||'currentColor'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>
);

Object.assign(window, {
  IconPlus, IconChevL, IconChevR, IconChevDown, IconBack, IconClose, IconCopy,
  IconCheck, IconText, IconCamera, IconWarn, IconError, IconFile, IconShare,
  IconCalendar, IconTrash, IconEdit, IconRetry, IconRefresh, IconImage, IconGear,
});
