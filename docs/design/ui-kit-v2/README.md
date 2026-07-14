# SiteTracker UI Kit

对应已确认的 v2 preview。三个文件:

| 文件 | 内容 |
|---|---|
| `theme.css` | Tailwind v4 semantic tokens(`@theme`) |
| `ui-kit.tsx` | Chip / Segmented / StatusBadge / BudgetBar / MarginSummary / IncompleteAmount / RateGapBanner / PrimaryButton + helpers |
| `TabBar.tsx` | 底部导航:Home · Jobs · 中央➕ · Labour |

## 设计规则速查

1. **实心 `primary` 只给动作**(Submit / Save / +New / 中央➕),每屏尽量只出现一个。
2. **选中态一律 tonal**:`bg-sel text-sel-text border-sel-border`,不用实心蓝。
3. **预算阈值**:<70% 绿 · 70–90% 琥珀 · ≥90% 红,标签统一 "X% used"。
4. **财务口径**:margin、预算条、成本全部按 **ex-GST**;Total paid 是现金流口径,单独展示。
5. **分类图配色避开交互蓝**:用 `cat-1 ~ cat-4`(绿 / teal / 橙 / 紫)。

## 接入

**Tailwind v4(推荐)**:把 `theme.css` 的 `@theme` 块并进主入口 css(已有 `@import "tailwindcss"` 就删掉文件顶部那行)。

**Tailwind v3 fallback**:不用 `theme.css`,在 `tailwind.config.js` 里加:

```js
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: "#2563EB", deep: "#1D4ED8" },
        sel: { DEFAULT: "#EFF6FF", text: "#1D4ED8", border: "#BFDBFE" },
        ok:   { DEFAULT: "#16A34A", bg: "#F0FDF4", border: "#BBF7D0" },
        warn: { DEFAULT: "#B45309", fill: "#D97706", bg: "#FFFBEB", border: "#FDE68A" },
        bad:  { DEFAULT: "#DC2626", bg: "#FEF2F2", border: "#FECACA" },
        ink:  { DEFAULT: "#0F172A", 2: "#475569", 3: "#94A3B8" },
        line: { DEFAULT: "#E2E8F0", soft: "#F1F5F9" },
        cat:  { 1: "#16A34A", 2: "#0D9488", 3: "#EA580C", 4: "#7C3AED" },
      },
    },
  },
};
```

类名两个版本完全一致(`bg-sel`、`text-ink-2`、`bg-warn-fill`…),组件代码无需改动。

## 用法示例

```tsx
import { TabBar } from "./TabBar";
import {
  Chip, Segmented, StatusBadge, BudgetBar,
  MarginSummary, IncompleteAmount, RateGapBanner, PrimaryButton,
} from "./ui-kit";

// 底部导航(记账页打开时传 active={null})
<TabBar active="jobs" onTab={(t) => nav(t)} onAdd={() => nav("/expense/new")} />

// Jobs 列表预算条(21 Castlehill:28% used,绿)
<BudgetBar spentExGst={30263.33} budgetExGst={110000} />

// Job details margin 卡(自动算 47.4% / +17.4pp / 到目前 85.5%)
<MarginSummary
  revenueExGst={209090.91}
  budgetExGst={110000}
  spentExGst={30263.33}
  targetPct={30}
/>

// 选中态
<Chip selected>21 Castlehill</Chip>
<Segmented
  options={[{ value: "month", label: "Month" }, { value: "week", label: "Week" }]}
  value={period}
  onChange={setPeriod}
/>

// 状态 badge(描述文字保持中性灰,状态只靠它表达)
<StatusBadge status="pending" />

// Labour cost 不完整标注 + 补 rate 入口(29 条里 26 条缺 rate)
<IncompleteAmount value={1190} incomplete />
<RateGapBanner missing={26} total={29} onFix={() => nav("/workers/rates")} />
```

## 注意

- 页面滚动容器留出底部空间,否则内容被 tab bar / FAB 盖住:
  `pb-[calc(78px+env(safe-area-inset-bottom))]`
- 金额统一 `tabular-nums`(helpers 里的 `money()` 已配合)。
- `RateGapBanner` 的 `missing` 是**缺 rate 的条数**:"3 of 29 entries have rate" ⇒ `missing={26}`。preview 演示图里写成了 "3/29 缺",方向反了,以组件语义为准。
