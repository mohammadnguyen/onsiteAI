import React from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { tokens } from './tokens';
import { HomeIcon, JobsIcon, LabourIcon, PlusIcon } from './icons';

/**
 * UI-kit v2 Batch 2: custom tab bar — Home · Jobs · [➕] · Labour
 * (RN translation of docs/design/ui-kit-v2/TabBar.tsx).
 *
 * The central ➕ is a raised 56px solid-primary FAB that pushes the
 * capture screen (app/capture.tsx) onto the root stack — the pushed
 * screen covers the tab bar entirely, which is the RN equivalent of
 * the web kit's `active={null}` state.
 *
 * Icons: react-native-svg (operator-approved), 1:1 from the design
 * kit. Labels come from each screen's `title` option, so they follow
 * the app language.
 *
 * Expects exactly the 3 tab routes declared in app/(tabs)/_layout.tsx
 * in order: home, jobs, labour (first two left of the FAB, the rest
 * right — mirrors the web kit's layout).
 */
const TAB_ICONS: Record<
  string,
  (p: { size?: number; color?: string }) => React.JSX.Element
> = { home: HomeIcon, jobs: JobsIcon, labour: LabourIcon };

export function AppTabBar({ state, descriptors, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useTranslation();
  // Double-tap guard (same window as useOneShotBack / the X-3 submit
  // guard): during the ~300ms push animation the bar stays tappable —
  // an unguarded second tap would stack two capture screens. Short
  // lockout, self-resetting.
  const fabLockRef = React.useRef(false);
  const onFabPress = () => {
    if (fabLockRef.current) return;
    fabLockRef.current = true;
    router.push('/capture' as unknown as Href);
    setTimeout(() => {
      fabLockRef.current = false;
    }, 800);
  };

  const renderTab = (route: (typeof state.routes)[number], index: number) => {
    const focused = state.index === index;
    const label =
      descriptors[route.key]?.options.title ?? route.name;
    const Icon = TAB_ICONS[route.name];
    const onPress = () => {
      const event = navigation.emit({
        type: 'tabPress',
        target: route.key,
        canPreventDefault: true,
      });
      if (!focused && !event.defaultPrevented) {
        navigation.navigate(route.name);
      }
    };
    return (
      <Pressable
        key={route.key}
        onPress={onPress}
        accessibilityRole="button"
        accessibilityState={{ selected: focused }}
        testID={`tab-${route.name}`}
        style={s.tab}
      >
        {Icon ? (
          <Icon size={21} color={focused ? tokens.primary : tokens.ink3} />
        ) : null}
        <Text
          style={[
            s.tabLabel,
            { color: focused ? tokens.primary : tokens.ink3 },
          ]}
          numberOfLines={1}
        >
          {label}
        </Text>
      </Pressable>
    );
  };

  return (
    <View style={[s.bar, { paddingBottom: insets.bottom }]}>
      <View style={s.inner}>
        <View style={s.side}>
          {state.routes.slice(0, 2).map((r, i) => renderTab(r, i))}
        </View>
        {/* gap reserved for the FAB */}
        <View style={s.fabGap} />
        <View style={s.side}>
          {state.routes.slice(2).map((r, i) => renderTab(r, i + 2))}
        </View>
        <Pressable
          onPress={onFabPress}
          accessibilityRole="button"
          accessibilityLabel={t('capture.title')}
          testID="tab-fab-capture"
          style={({ pressed }) => [s.fab, pressed && s.fabPressed]}
        >
          <PlusIcon size={26} color="#ffffff" />
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  bar: {
    borderTopWidth: 1,
    borderTopColor: tokens.line,
    backgroundColor: '#ffffff',
  },
  inner: {
    height: 62,
    flexDirection: 'row',
    alignItems: 'stretch',
    paddingHorizontal: 6,
  },
  side: { flex: 1, flexDirection: 'row' },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 3 },
  tabLabel: { fontSize: 11, fontWeight: '600' },
  fabGap: { width: 76 },
  // NOTE (accepted, review nit): the raised top 16px of the FAB sits
  // outside the bar's bounds — RN hit-testing ignores that strip, so
  // the effective target is the lower ~40px. Verified acceptable.
  fab: {
    position: 'absolute',
    left: '50%',
    marginLeft: -28,
    top: -16,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: tokens.primary,
    alignItems: 'center',
    justifyContent: 'center',
    // iOS shadow per the design; elevation for Android.
    shadowColor: tokens.primary,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.38,
    shadowRadius: 18,
    elevation: 8,
  },
  fabPressed: { backgroundColor: tokens.primaryDeep, transform: [{ scale: 0.95 }] },

});
