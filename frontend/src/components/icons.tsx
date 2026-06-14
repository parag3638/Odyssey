import type { SVGProps } from "react";

const stroke: SVGProps<SVGSVGElement> = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
};

export function SailIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor">
      <path d="M12.7 2.4C16.7 6.4 18.4 11 18.4 14.8L12.7 14.8Z M4.8 16.6H19.2L17 20.6H7Z" />
    </svg>
  );
}

export function OverviewIcon() {
  return (
    <svg {...stroke}>
      <path d="M3 10.5 12 4l9 6.5" />
      <path d="M5 9.5V20h14V9.5" />
    </svg>
  );
}

export function PositionsIcon() {
  return (
    <svg {...stroke}>
      <path d="M4 19V5M4 19l5-5 4 3 7-8M20 9V5h-4" />
    </svg>
  );
}

export function SignalsIcon() {
  return (
    <svg {...stroke}>
      <path d="m3 11 18-7-7 18-2.5-8L3 11Z" />
    </svg>
  );
}

export function ActivityIcon() {
  return (
    <svg {...stroke}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}

export function SunIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" />
    </svg>
  );
}

export function ChevronDownIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function ArrowRightIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M2 12s3.6-7 10-7c2 0 3.7.7 5.2 1.6M22 12s-3.6 7-10 7c-2 0-3.7-.7-5.2-1.6" />
      <path d="m4 4 16 16" />
    </svg>
  );
}

export function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

export function BotPulseIcon() {
  return (
    <svg {...stroke}>
      <path d="M4 14h4l2-7 3 14 2-9 2 4h3" />
    </svg>
  );
}

export function SparklesIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M5 3v4M3 5h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5z" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function StocksIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <path d="M4 19V5M9 19V10M14 19v-6M19 19V8" />
    </svg>
  );
}

export function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

export function ArrowUpRightIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
      <path d="M7 17 17 7M9 7h8v8" />
    </svg>
  );
}

export function ArrowDownRightIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
      <path d="M7 7 17 17M17 9v8H9" />
    </svg>
  );
}

export function FilterIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <path d="M3 6h18M7 12h10M11 18h2" />
    </svg>
  );
}

export function ColumnsIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16M15 4v16" />
    </svg>
  );
}

export function GripIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor">
      <circle cx="9" cy="6" r="1.4" />
      <circle cx="15" cy="6" r="1.4" />
      <circle cx="9" cy="12" r="1.4" />
      <circle cx="15" cy="12" r="1.4" />
      <circle cx="9" cy="18" r="1.4" />
      <circle cx="15" cy="18" r="1.4" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}>
      <path d="m5 12 5 5 9-11" />
    </svg>
  );
}

export function XIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function ExpandIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}

export function StarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path d="m12 3 2.5 6 6.5.5-5 4.3 1.6 6.4L12 17l-5.6 3.2L8 13.8l-5-4.3 6.5-.5z" />
    </svg>
  );
}

export function BookmarkIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1Z" />
    </svg>
  );
}

export function GiftIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <rect x="3" y="8" width="18" height="4" rx="1" />
      <path d="M5 12v9h14v-9M12 8v13M12 8S10 3 7.5 4.5 9 8 12 8Zm0 0s2-5 4.5-3.5S15 8 12 8Z" />
    </svg>
  );
}

export function SwapIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <path d="M7 4 3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8" />
    </svg>
  );
}

export function CalendarIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 9h18M8 3v4M16 3v4" />
    </svg>
  );
}

export function NewspaperIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <path d="M4 5h13v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
      <path d="M17 8h3v11a2 2 0 0 1-2 2M7 9h7M7 13h7M7 17h4" />
    </svg>
  );
}

export function LightningIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
    </svg>
  );
}

export function DiamondIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}>
      <path d="M6 3h12l3 6-9 12L3 9l3-6ZM3 9h18M9 3 7 9l5 12 5-12-2-6" />
    </svg>
  );
}

export function BriefcaseIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" />
    </svg>
  );
}

export function BankIcon() {
  return (
    <svg {...stroke} strokeWidth={1.7}>
      <path d="M3 9 12 4l9 5M4 9v9M20 9v9M8 9v9M16 9v9M3 21h18" />
    </svg>
  );
}

export function DollarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M12 2v20M16.5 7A4 4 0 0 0 12.5 4h-1a3.5 3.5 0 0 0 0 7h1a3.5 3.5 0 0 1 0 7h-1A4 4 0 0 1 7.5 17" />
    </svg>
  );
}

export function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  );
}

export function AlertCircleIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  );
}

export function InfoIcon() {
  return (
    <svg {...stroke} strokeWidth={1.8}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  );
}
