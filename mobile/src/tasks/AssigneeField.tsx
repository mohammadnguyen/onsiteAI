import { useMemo, useState } from 'react';
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

import { useUsers } from '../api/hooks/useUsers';
import { Chip } from '../ui/kit';
import { tokens } from '../ui/tokens';

type Person = { user_id: string; full_name: string };

/**
 * Who a task is assigned to: at most one person, or nobody.
 *
 * D1 decides the choice, and the server enforces it (422 otherwise): an
 * admin may pick any active user from the existing admin-only user list;
 * anyone else may pick only themselves or nobody, so no one but an admin is
 * ever shown a list of people.
 */
export function AssigneeField({
  value,
  valueName,
  me,
  assignOthers,
  disabled,
  onChange,
}: {
  value: string | null;
  valueName: string | null;
  me: Person | null;
  assignOthers: boolean;
  disabled: boolean;
  onChange: (userId: string | null, name: string | null) => void;
}) {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);

  if (!assignOthers) {
    const someoneElse = value !== null && value !== me?.user_id;
    return (
      <View style={s.row}>
        {me ? (
          <Chip
            label={t('tasks.assignee.me')}
            selected={value === me.user_id}
            disabled={disabled}
            onPress={() => onChange(me.user_id, me.full_name)}
            testID="assignee-me"
          />
        ) : null}
        <Chip
          label={t('tasks.assignee.unassigned')}
          selected={value === null}
          disabled={disabled}
          onPress={() => onChange(null, null)}
          testID="assignee-none"
        />
        {someoneElse ? (
          // Set by an admin. Shown so the task reads truthfully; it can be
          // cleared, not given to someone else, from here.
          <Chip label={valueName ?? t('tasks.assignee.someone')} selected disabled />
        ) : null}
      </View>
    );
  }

  return (
    <View style={s.adminRow}>
      <Text style={s.current} testID="assignee-current">
        {value === null ? t('tasks.assignee.unassigned') : (valueName ?? t('tasks.assignee.someone'))}
      </Text>
      <Pressable
        onPress={() => !disabled && setPicking(true)}
        disabled={disabled}
        accessibilityRole="button"
        hitSlop={8}
        testID="assignee-change"
      >
        <Text style={[s.link, disabled ? s.dim : null]}>{t('tasks.assignee.change')}</Text>
      </Pressable>
      <PeopleSheet
        visible={picking}
        selected={value}
        onClose={() => setPicking(false)}
        onSelect={(id, name) => {
          setPicking(false);
          onChange(id, name);
        }}
      />
    </View>
  );
}

function PeopleSheet({
  visible,
  selected,
  onClose,
  onSelect,
}: {
  visible: boolean;
  selected: string | null;
  onClose: () => void;
  onSelect: (userId: string | null, name: string | null) => void;
}) {
  const { t } = useTranslation();
  const users = useUsers();
  // Active users only: the server refuses anyone else.
  const people = useMemo(
    () =>
      (users.data ?? [])
        .filter((u) => u.is_active)
        .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [users.data],
  );
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={s.sheet} edges={['top', 'bottom']}>
        <View style={s.sheetHead}>
          <Text style={s.sheetTitle}>{t('tasks.assignee.pick_title')}</Text>
          <Pressable onPress={onClose} hitSlop={12} accessibilityRole="button">
            <Text style={s.link}>{t('common.close')}</Text>
          </Pressable>
        </View>
        {users.isError ? <Text style={s.note}>{t('tasks.assignee.load_failed')}</Text> : null}
        <FlatList
          data={[{ user_id: '', full_name: t('tasks.assignee.unassigned') }, ...people]}
          keyExtractor={(p) => p.user_id || 'none'}
          renderItem={({ item }) => {
            const id = item.user_id || null;
            const on = id === selected;
            return (
              <Pressable
                style={[s.person, on ? s.personOn : null]}
                onPress={() => onSelect(id, id ? item.full_name : null)}
                accessibilityRole="button"
                accessibilityState={{ selected: on }}
                testID={`assignee-option:${item.user_id || 'none'}`}
              >
                <Text style={[s.personName, on ? s.personNameOn : null]}>{item.full_name}</Text>
              </Pressable>
            );
          }}
        />
      </SafeAreaView>
    </Modal>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  adminRow: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  current: { color: tokens.ink, fontSize: 15, flexShrink: 1 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  dim: { opacity: 0.4 },
  sheet: { flex: 1, backgroundColor: tokens.bg },
  sheetHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 16,
  },
  sheetTitle: { fontSize: 18, fontWeight: '700', color: tokens.ink },
  note: { color: tokens.warnMid, paddingHorizontal: 16, paddingBottom: 8 },
  person: {
    marginHorizontal: 16,
    marginBottom: 8,
    padding: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  personOn: { borderColor: tokens.selBorder, backgroundColor: tokens.sel },
  personName: { color: tokens.ink, fontSize: 15 },
  personNameOn: { color: tokens.selText, fontWeight: '600' },
});
