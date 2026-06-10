import {
  Modal,
  View,
  Text,
  Pressable,
  FlatList,
  StyleSheet,
} from 'react-native';

/**
 * M2-B: generic single-select bottom-sheet picker for the full
 * expenses list's filter chips (job / status / date preset /
 * supplier / category).
 *
 * Deliberately minimal and phone-native: a slide-up sheet with a
 * plain option list and a cancel row — no search, no multi-select
 * (the supplier/category lists are small for a single-builder
 * tenant; a searchable picker is a later polish item if dogfooding
 * demands it).
 *
 * `value: null` is the conventional "All" option — the caller puts
 * it first in `options` and clears its filter when selected.
 */

export type PickerOption = { value: string | null; label: string };

export function OptionPickerModal({
  visible,
  title,
  options,
  selected,
  onSelect,
  onClose,
  cancelLabel,
}: {
  visible: boolean;
  title: string;
  options: PickerOption[];
  selected: string | null;
  onSelect: (value: string | null) => void;
  onClose: () => void;
  cancelLabel: string;
}) {
  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <View style={s.backdrop}>
        <View style={s.sheet} testID="option-picker">
          <Text style={s.title}>{title}</Text>
          <FlatList
            data={options}
            keyExtractor={(o) => o.value ?? '__all__'}
            style={s.list}
            renderItem={({ item }) => (
              <Pressable
                onPress={() => {
                  onSelect(item.value);
                  onClose();
                }}
                accessibilityRole="button"
                testID={`option-${item.value ?? 'all'}`}
                style={({ pressed }) => [s.row, pressed && s.rowPressed]}
              >
                <Text
                  style={[
                    s.rowText,
                    selected === item.value && s.rowTextActive,
                  ]}
                  numberOfLines={1}
                >
                  {item.label}
                </Text>
                {selected === item.value ? (
                  <Text style={s.check}>{'✓'}</Text>
                ) : null}
              </Pressable>
            )}
          />
          <Pressable
            onPress={onClose}
            accessibilityRole="button"
            testID="option-picker-cancel"
            style={({ pressed }) => [s.cancel, pressed && s.rowPressed]}
          >
            <Text style={s.cancelText}>{cancelLabel}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(15, 23, 42, 0.4)',
  },
  sheet: {
    backgroundColor: '#ffffff',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingTop: 16,
    paddingBottom: 24,
    maxHeight: '70%',
  },
  title: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0f172a',
    paddingHorizontal: 16,
    marginBottom: 8,
  },
  list: { flexGrow: 0 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  rowPressed: { backgroundColor: '#f1f5f9' },
  rowText: { fontSize: 15, color: '#0f172a', flexShrink: 1 },
  rowTextActive: { fontWeight: '700' },
  check: { fontSize: 15, color: '#15803d', fontWeight: '700', marginLeft: 8 },
  cancel: { paddingVertical: 14, alignItems: 'center' },
  cancelText: { fontSize: 15, color: '#64748b', fontWeight: '600' },
});
