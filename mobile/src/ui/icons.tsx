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
