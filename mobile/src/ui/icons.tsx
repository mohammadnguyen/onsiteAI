import React from 'react';
import Svg, { Path, Rect, Circle } from 'react-native-svg';

/**
 * UI-kit v2 icon set (react-native-svg — operator-approved dependency).
 * Tab + FAB icons are 1:1 translations of docs/design/ui-kit-v2/
 * TabBar.tsx; the stat-card glyphs (dollar/clock/briefcase/users) are
 * feather-style strokes matching the preview cards. All icons inherit
 * `color` (stroke) so callers control tint; size via width/height.
 */

type IconProps = { size?: number; color?: string; strokeWidth?: number };

export function HomeIcon({ size = 21, color = '#94A3B8', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M3 10.8 12 3.4l9 7.4" />
      <Path d="M5.3 9.2V20h13.4V9.2" />
      <Path d="M9.8 20v-5.6h4.4V20" />
    </Svg>
  );
}

export function JobsIcon({ size = 21, color = '#94A3B8', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth}>
      <Rect x={3} y={7.5} width={18} height={12.5} rx={2.5} />
      <Path d="M8.5 7.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5" />
    </Svg>
  );
}

export function LabourIcon({ size = 21, color = '#94A3B8', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round">
      <Circle cx={9} cy={8} r={3.2} />
      <Path d="M3.4 19.2c.6-3.1 2.9-4.7 5.6-4.7s5 1.6 5.6 4.7" />
      <Circle cx={17.2} cy={9} r={2.5} />
      <Path d="M15.8 14.8c2.3.3 4 1.7 4.7 4.4" />
    </Svg>
  );
}

export function PlusIcon({ size = 26, color = '#ffffff', strokeWidth = 2.4 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round">
      <Path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function GearIcon({ size = 20, color = '#475569', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Circle cx={12} cy={12} r={3.1} />
      <Path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.08a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z" />
    </Svg>
  );
}

export function DollarIcon({ size = 16, color = '#16A34A', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round">
      <Path d="M12 2v20" />
      <Path d="M17 5.5H9.8a3.3 3.3 0 0 0 0 6.6h4.4a3.3 3.3 0 0 1 0 6.6H6.5" />
    </Svg>
  );
}

export function ClockIcon({ size = 16, color = '#D97706', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Circle cx={12} cy={12} r={9} />
      <Path d="M12 7v5l3.2 1.8" />
    </Svg>
  );
}

export function BriefcaseIcon({ size = 16, color = '#2563EB', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth}>
      <Rect x={3} y={7.5} width={18} height={12.5} rx={2.5} />
      <Path d="M8.5 7.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5" />
    </Svg>
  );
}

export function UsersIcon({ size = 16, color = '#EA580C', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round">
      <Circle cx={9} cy={8} r={3.2} />
      <Path d="M3.4 19.2c.6-3.1 2.9-4.7 5.6-4.7s5 1.6 5.6 4.7" />
      <Circle cx={17.2} cy={9} r={2.5} />
      <Path d="M15.8 14.8c2.3.3 4 1.7 4.7 4.4" />
    </Svg>
  );
}

/* ================= forey additions (F0) =================
 * Line icons per the forey handoff (README §Assets): stroke 1.8-2.4,
 * round cap/join; 21px nav / 19px entry / 18px form. Plus the brand
 * logo — "框架 F" = blue rounded square with three white bars.
 */

export function MicIcon({ size = 18, color = '#98A2B3', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Rect x={9} y={2.5} width={6} height={11.5} rx={3} />
      <Path d="M5.5 11.5a6.5 6.5 0 0 0 13 0" />
      <Path d="M12 18v3.5" />
    </Svg>
  );
}

export function ReceiptIcon({ size = 19, color = '#667085', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M6 2.5h12v19l-2.4-1.7-2.4 1.7-1.2-.9-1.2.9-2.4-1.7L6 21.5Z" />
      <Path d="M9.5 8h5M9.5 12h5" />
    </Svg>
  );
}

export function FolderIcon({ size = 19, color = '#667085', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z" />
    </Svg>
  );
}

export function NoteIcon({ size = 19, color = '#667085', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M5 3.5h14v13l-4 4H5Z" />
      <Path d="M15 20.5v-4h4" />
      <Path d="M8.5 9h7M8.5 12.5h4.5" />
    </Svg>
  );
}

export function SearchIcon({ size = 18, color = '#98A2B3', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round">
      <Circle cx={10.5} cy={10.5} r={6.5} />
      <Path d="m15.3 15.3 5.2 5.2" />
    </Svg>
  );
}

export function CameraIcon({ size = 18, color = '#667085', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M4 8.5a2 2 0 0 1 2-2h1.6l1.6-2.5h5.6l1.6 2.5H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
      <Circle cx={12} cy={13} r={3.4} />
    </Svg>
  );
}

export function SendIcon({ size = 16, color = '#ffffff', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M21 3.5 10.5 14" />
      <Path d="M21 3.5 14 21l-3.5-7L3.5 10.5Z" />
    </Svg>
  );
}

export function ImageIcon({ size = 19, color = '#667085', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Rect x={3.5} y={4.5} width={17} height={15} rx={2.5} />
      <Circle cx={9} cy={10} r={1.8} />
      <Path d="m20.5 15.5-4.5-4.5-8 8.5" />
    </Svg>
  );
}

export function FaceIdIcon({ size = 18, color = '#2563EB', strokeWidth = 2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8M16 3h2.5A2.5 2.5 0 0 1 21 5.5V8M21 16v2.5a2.5 2.5 0 0 1-2.5 2.5H16M8 21H5.5A2.5 2.5 0 0 1 3 18.5V16" />
      <Path d="M8.5 9.2v1.6M15.5 9.2v1.6" />
      <Path d="M12 9.5v3.6h-1" />
      <Path d="M8.7 15.6c.9.9 2 1.4 3.3 1.4s2.4-.5 3.3-1.4" />
    </Svg>
  );
}

export function MailIcon({ size = 18, color = '#98A2B3', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Rect x={3} y={5} width={18} height={14} rx={2.5} />
      <Path d="m4 7.5 8 6 8-6" />
    </Svg>
  );
}

export function LockIcon({ size = 18, color = '#98A2B3', strokeWidth = 1.9 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Rect x={4.5} y={10.5} width={15} height={10} rx={2.5} />
      <Path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
      <Circle cx={12} cy={15.5} r={1.4} />
    </Svg>
  );
}

export function ChevronRightIcon({ size = 15, color = '#98A2B3', strokeWidth = 2.2 }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <Path d="m9 5 7 7-7 7" />
    </Svg>
  );
}

/**
 * forey brand logo — 方案A「框架 F」: blue rounded square (#2563EB,
 * radius ≈ 26%) with three white rounded bars. Sizes per spec: login
 * 76 (radius 19), Home header 30 (radius 8).
 */
export function ForeyLogo({ size = 30 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 96 96">
      <Rect x={0} y={0} width={96} height={96} rx={25} fill="#2563EB" />
      <Rect x={27} y={17} width={15} height={62} rx={4} fill="#ffffff" />
      <Rect x={46} y={17} width={26} height={15} rx={4} fill="#ffffff" />
      <Rect x={46} y={40} width={18} height={15} rx={4} fill="#ffffff" />
    </Svg>
  );
}
