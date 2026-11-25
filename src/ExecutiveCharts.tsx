import { useId, useState } from 'react';
import { cny } from './value-baseline';
import './executive-charts.css';

export const CHART_PALETTE = [
  '#2f7d5b',
  '#d49b38',
  '#3e82ad',
  '#c96a4b',
  '#6e6ab8',
  '#4f9a83',
] as const;

export const WALL_CHART_PALETTE = [
  '#4fd19b',
  '#f0c36a',
  '#67b8e8',
  '#f08a6c',
  '#a59df0',
  '#7ee0c0',
] as const;

/** 面向柱顶与纵轴的大白话简洁金额格式，悬停浮窗仍使用精确到分的 cny()。 */
export function formatCompactCny(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '核算中';
  const sign = cents < 0 ? '-' : '';
  const absYuan = Math.abs(cents) / 100;
  if (absYuan >= 100_000_000) return `${sign}${(absYuan / 100_000_000).toFixed(2)}亿`;
  if (absYuan >= 10_000) {
    const wan = absYuan / 10_000;
    return `${sign}${wan >= 100 ? wan.toFixed(1) : wan.toFixed(2)}万`;
  }
  if (absYuan === 0) return '0元';
  return `${sign}${Math.round(absYuan).toLocaleString('zh-CN')}元`;
}

function smoothPath(points: { x: number; y: number }[]): string {
  if (!points.length) return '';
  if (points.length === 1) return `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
  let d = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i];
    const p1 = points[i + 1];
    const cx = (p0.x + p1.x) / 2;
    d += ` C ${cx.toFixed(1)} ${p0.y.toFixed(1)}, ${cx.toFixed(1)} ${p1.y.toFixed(1)}, ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
  }
  return d;
}

function areaPath(points: { x: number; y: number }[], baselineY: number): string {
  if (points.length < 2) return '';
  const line = smoothPath(points);
  const last = points[points.length - 1];
  const first = points[0];
  return `${line} L ${last.x.toFixed(1)} ${baselineY.toFixed(1)} L ${first.x.toFixed(1)} ${baselineY.toFixed(1)} Z`;
}

export interface TrendSeries {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
  area?: boolean;
}

export function TrendAreaChart({
  labels,
  series,
  valueFormatter = (value) => cny(value),
  axisFormatter = (value) => formatCompactCny(value),
  ariaLabel,
  emptyText = '暂无可用走势数据',
  note,
  height = 228,
}: {
  labels: string[];
  series: TrendSeries[];
  valueFormatter?: (value: number) => string;
  axisFormatter?: (value: number) => string;
  ariaLabel: string;
  emptyText?: string;
  note?: string;
  height?: number;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const allValues = series
    .flatMap((s) => s.values)
    .filter((v): v is number => v !== null && Number.isFinite(v));

  if (!labels.length || !allValues.length) {
    return <p className="exec-chart-empty">{emptyText}</p>;
  }

  const width = 660;
  const pad = { left: 66, right: 20, top: 20, bottom: 32 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const rawMin = Math.min(0, ...allValues);
  const rawMax = Math.max(0, ...allValues);
  const span = Math.max(1, rawMax - rawMin);
  const maxVal = rawMax + span * 0.12;
  const minVal = rawMin < 0 ? rawMin - span * 0.1 : 0;
  const range = Math.max(1, maxVal - minVal);

  const xAt = (idx: number) =>
    labels.length === 1 ? pad.left + plotW / 2 : pad.left + (idx * plotW) / (labels.length - 1);
  const yAt = (val: number) => pad.top + ((maxVal - val) / range) * plotH;
  const zeroY = yAt(0);

  const ticks = [maxVal, minVal + range * 0.5, minVal];

  const splitSegments = (values: (number | null)[]) => {
    const segs: { x: number; y: number; index: number; value: number }[][] = [];
    let current: { x: number; y: number; index: number; value: number }[] = [];
    values.forEach((v, idx) => {
      if (v === null || !Number.isFinite(v)) {
        if (current.length) segs.push(current);
        current = [];
      } else {
        current.push({ x: xAt(idx), y: yAt(v), index: idx, value: v });
      }
    });
    if (current.length) segs.push(current);
    return segs;
  };

  const activeIdx = hoverIndex !== null && hoverIndex < labels.length ? hoverIndex : null;

  return (
    <div className="exec-chart-box">
      <div className="exec-chart-svg-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
          <defs>
            {series.map((s) => (
              <linearGradient
                key={s.key}
                id={`grad-${uid}-${s.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={s.color} stopOpacity="0.28" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0.02" />
              </linearGradient>
            ))}
          </defs>

          {ticks.map((tick, idx) => {
            const y = yAt(tick);
            return (
              <g key={idx}>
                <line
                  x1={pad.left}
                  x2={width - pad.right}
                  y1={y}
                  y2={y}
                  className="exec-chart-grid"
                />
                <text x={pad.left - 8} y={y + 4} textAnchor="end" className="exec-chart-axis">
                  {axisFormatter(tick)}
                </text>
              </g>
            );
          })}

          {minVal < 0 && (
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={zeroY}
              y2={zeroY}
              className="exec-chart-zero"
            />
          )}

          {series.map((s) => {
            const segments = splitSegments(s.values);
            return (
              <g key={s.key}>
                {s.area &&
                  segments.map((seg, i) =>
                    seg.length >= 2 ? (
                      <path
                        key={`area-${i}`}
                        d={areaPath(seg, zeroY)}
                        fill={`url(#grad-${uid}-${s.key})`}
                      />
                    ) : null,
                  )}
                {segments.map((seg, i) => (
                  <path
                    key={`line-${i}`}
                    d={smoothPath(seg)}
                    fill="none"
                    stroke={s.color}
                    strokeWidth="2.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
                {segments.flat().map((pt) => (
                  <circle
                    key={`pt-${pt.index}`}
                    cx={pt.x}
                    cy={pt.y}
                    r={activeIdx === pt.index ? 5.5 : 3.5}
                    fill={s.color}
                    className="exec-chart-point"
                  />
                ))}
              </g>
            );
          })}

          {activeIdx !== null && (
            <line
              x1={xAt(activeIdx)}
              x2={xAt(activeIdx)}
              y1={pad.top}
              y2={height - pad.bottom}
              className="exec-chart-guide"
            />
          )}

          {labels.map((label, idx) => {
            const colX = xAt(idx);
            const sliceW = labels.length > 1 ? plotW / (labels.length - 1) : plotW;
            return (
              <g key={`${label}-${idx}`}>
                <text x={colX} y={height - 10} textAnchor="middle" className="exec-chart-axis">
                  {label}
                </text>
                <rect
                  x={Math.max(pad.left - 12, colX - sliceW / 2)}
                  y={pad.top}
                  width={sliceW}
                  height={plotH + 18}
                  fill="transparent"
                  className="exec-chart-hit"
                  onMouseEnter={() => setHoverIndex(idx)}
                  onMouseLeave={() => setHoverIndex(null)}
                />
              </g>
            );
          })}
        </svg>

        {activeIdx !== null && (
          <div className="exec-chart-tooltip" role="status">
            <strong>{labels[activeIdx]}</strong>
            {series.map((s) => {
              const v = s.values[activeIdx];
              return (
                <div key={s.key} className="exec-chart-tooltip-row">
                  <span>
                    <i style={{ background: s.color }} />
                    {s.label}
                  </span>
                  <b>{v === null || v === undefined ? '未复核' : valueFormatter(v)}</b>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="exec-chart-legend">
        {series.map((s) => (
          <span key={s.key}>
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
        {note && <span className="exec-chart-note">{note}</span>}
      </div>
    </div>
  );
}

export interface DonutSlice {
  id: string;
  label: string;
  value: number;
  displayValue: string;
  color: string;
  clickable?: boolean;
}

export function DonutPieChart({
  slices,
  centerLabel,
  centerValue,
  ariaLabel,
  emptyText = '暂无可展示的结构占比数据',
  onSelect,
}: {
  slices: DonutSlice[];
  centerLabel: string;
  centerValue: string;
  ariaLabel: string;
  emptyText?: string;
  onSelect?: (id: string) => void;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const positive = slices.filter((item) => item.value > 0);
  const total = positive.reduce((sum, item) => sum + item.value, 0);

  if (!positive.length || total <= 0) {
    return <p className="exec-chart-empty">{emptyText}</p>;
  }

  const size = 212;
  const cx = size / 2;
  const cy = size / 2;
  const outerR = 86;
  const innerR = 56;

  let startAngle = -Math.PI / 2;
  const arcs = positive.map((slice) => {
    const ratio = slice.value / total;
    const sweep = Math.max(0.02, ratio * Math.PI * 2);
    const endAngle = startAngle + sweep;
    const midAngle = startAngle + sweep / 2;
    const largeArc = sweep > Math.PI ? 1 : 0;

    // 单一满圆时使用完整圆环兜底
    const isFull = ratio >= 0.999;
    const x1 = cx + outerR * Math.cos(startAngle);
    const y1 = cy + outerR * Math.sin(startAngle);
    const x2 = cx + outerR * Math.cos(endAngle);
    const y2 = cy + outerR * Math.sin(endAngle);
    const ix1 = cx + innerR * Math.cos(endAngle);
    const iy1 = cy + innerR * Math.sin(endAngle);
    const ix2 = cx + innerR * Math.cos(startAngle);
    const iy2 = cy + innerR * Math.sin(startAngle);

    const d = isFull
      ? ''
      : [
          `M ${x1.toFixed(2)} ${y1.toFixed(2)}`,
          `A ${outerR} ${outerR} 0 ${largeArc} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
          `L ${ix1.toFixed(2)} ${iy1.toFixed(2)}`,
          `A ${innerR} ${innerR} 0 ${largeArc} 0 ${ix2.toFixed(2)} ${iy2.toFixed(2)}`,
          'Z',
        ].join(' ');

    const result = {
      ...slice,
      ratio,
      pct: `${(ratio * 100).toFixed(1)}%`,
      d,
      isFull,
      dx: Math.cos(midAngle) * 5,
      dy: Math.sin(midAngle) * 5,
    };
    startAngle = endAngle;
    return result;
  });

  const active = arcs.find((item) => item.id === activeId) ?? null;

  return (
    <div className="exec-donut-layout">
      <div className="exec-donut-canvas">
        <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={ariaLabel}>
          {arcs.map((arc) => {
            const isHovered = active?.id === arc.id;
            const clickable = !!onSelect && arc.clickable !== false;
            const transform = isHovered
              ? `translate(${arc.dx.toFixed(1)}, ${arc.dy.toFixed(1)})`
              : undefined;
            if (arc.isFull) {
              return (
                <circle
                  key={arc.id}
                  cx={cx}
                  cy={cy}
                  r={(outerR + innerR) / 2}
                  fill="none"
                  stroke={arc.color}
                  strokeWidth={outerR - innerR}
                  className={clickable ? 'exec-donut-slice clickable' : 'exec-donut-slice'}
                  onMouseEnter={() => setActiveId(arc.id)}
                  onMouseLeave={() => setActiveId(null)}
                  onClick={() => clickable && onSelect?.(arc.id)}
                />
              );
            }
            return (
              <path
                key={arc.id}
                d={arc.d}
                fill={arc.color}
                transform={transform}
                className={
                  'exec-donut-slice' +
                  (isHovered ? ' active' : '') +
                  (clickable ? ' clickable' : '')
                }
                onMouseEnter={() => setActiveId(arc.id)}
                onMouseLeave={() => setActiveId(null)}
                onClick={() => clickable && onSelect?.(arc.id)}
              />
            );
          })}
        </svg>
        <div className="exec-donut-center">
          <span>{active ? active.label : centerLabel}</span>
          <strong>{active ? active.displayValue : centerValue}</strong>
          {active && <small>占比 {active.pct}</small>}
        </div>
      </div>

      <ul className="exec-donut-legend">
        {arcs.map((arc) => {
          const clickable = !!onSelect && arc.clickable !== false;
          const isHovered = active?.id === arc.id;
          return (
            <li key={arc.id}>
              <button
                type="button"
                className={'exec-donut-legend-btn' + (isHovered ? ' active' : '')}
                disabled={!clickable}
                onMouseEnter={() => setActiveId(arc.id)}
                onMouseLeave={() => setActiveId(null)}
                onFocus={() => setActiveId(arc.id)}
                onBlur={() => setActiveId(null)}
                onClick={() => clickable && onSelect?.(arc.id)}
              >
                <i style={{ background: arc.color }} />
                <span className="exec-donut-name">{arc.label}</span>
                <span className="exec-donut-pct">{arc.pct}</span>
                <strong>{arc.displayValue}</strong>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export interface BarGroupItem {
  id: string;
  label: string;
  known: boolean;
  revenueCents: number;
  expensesCents: number;
  profitCents: number;
}

export function GroupedBarChart({
  items,
  ariaLabel,
  emptyText = '暂无可对比的子公司收支数据',
  onSelect,
  presentation = false,
}: {
  items: BarGroupItem[];
  ariaLabel: string;
  emptyText?: string;
  onSelect?: (id: string) => void;
  presentation?: boolean;
}) {
  const [hoverId, setHoverId] = useState<string | null>(null);

  if (!items.length) {
    return <p className="exec-chart-empty">{emptyText}</p>;
  }

  const knownItems = items.filter((item) => item.known);
  const maxAbs = Math.max(
    100,
    ...knownItems.flatMap((item) => [
      Math.abs(item.revenueCents),
      Math.abs(item.expensesCents),
      Math.abs(item.profitCents),
    ]),
  );

  const width = Math.max(540, items.length * 116);
  const height = 244;
  const pad = { left: 62, right: 18, top: 24, bottom: 42 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const groupW = plotW / items.length;
  const barW = Math.min(22, Math.max(10, (groupW - 26) / 3));
  const barGap = 4;

  const colors = presentation
    ? { revenue: '#4fd19b', expenses: '#f0c36a', profit: '#67b8e8', loss: '#f07167' }
    : { revenue: '#246b4f', expenses: '#c88c2c', profit: '#2d6fa3', loss: '#b83c34' };

  const active = items.find((item) => item.id === hoverId) ?? null;

  return (
    <div className="exec-chart-box">
      <div className="exec-chart-svg-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
          {[1, 0.5, 0].map((ratio) => {
            const val = maxAbs * ratio;
            const y = pad.top + (1 - ratio) * plotH;
            return (
              <g key={ratio}>
                <line
                  x1={pad.left}
                  x2={width - pad.right}
                  y1={y}
                  y2={y}
                  className="exec-chart-grid"
                />
                <text x={pad.left - 8} y={y + 4} textAnchor="end" className="exec-chart-axis">
                  {formatCompactCny(val)}
                </text>
              </g>
            );
          })}

          {items.map((item, idx) => {
            const centerX = pad.left + idx * groupW + groupW / 2;
            const totalBarsW = barW * 3 + barGap * 2;
            const startX = centerX - totalBarsW / 2;
            const isHovered = hoverId === item.id;
            const shortName = item.label.length > 6 ? `${item.label.slice(0, 6)}…` : item.label;

            const bars = [
              { key: 'rev', value: item.revenueCents, color: colors.revenue },
              { key: 'exp', value: item.expensesCents, color: colors.expenses },
              {
                key: 'prf',
                value: item.profitCents,
                color: item.profitCents < 0 ? colors.loss : colors.profit,
              },
            ];

            return (
              <g
                key={item.id}
                className={'exec-bar-group' + (onSelect ? ' clickable' : '')}
                onMouseEnter={() => setHoverId(item.id)}
                onMouseLeave={() => setHoverId(null)}
                onClick={() => onSelect?.(item.id)}
              >
                {isHovered && (
                  <rect
                    x={pad.left + idx * groupW + 4}
                    y={pad.top - 4}
                    width={groupW - 8}
                    height={plotH + 8}
                    rx="8"
                    className="exec-bar-hover-bg"
                  />
                )}
                {item.known ? (
                  bars.map((bar, bIdx) => {
                    const h = Math.max(4, (Math.abs(bar.value) / maxAbs) * (plotH - 16));
                    const x = startX + bIdx * (barW + barGap);
                    const y = pad.top + plotH - h;
                    return (
                      <g key={bar.key}>
                        <rect x={x} y={y} width={barW} height={h} rx="4" fill={bar.color} />
                        {bIdx === 0 && (
                          <text
                            x={centerX}
                            y={Math.max(14, y - 6)}
                            textAnchor="middle"
                            className="exec-bar-top-val"
                          >
                            {formatCompactCny(item.revenueCents)}
                          </text>
                        )}
                      </g>
                    );
                  })
                ) : (
                  <g>
                    <rect
                      x={startX}
                      y={pad.top + plotH - 28}
                      width={totalBarsW}
                      height={28}
                      rx="6"
                      className="exec-bar-pending"
                    />
                    <text
                      x={centerX}
                      y={pad.top + plotH - 10}
                      textAnchor="middle"
                      className="exec-chart-axis"
                    >
                      核算中
                    </text>
                  </g>
                )}
                <text x={centerX} y={height - 14} textAnchor="middle" className="exec-chart-axis">
                  {shortName}
                </text>
              </g>
            );
          })}
        </svg>

        {active && (
          <div className="exec-chart-tooltip" role="status">
            <strong>{active.label}</strong>
            {active.known ? (
              <>
                <div className="exec-chart-tooltip-row">
                  <span>
                    <i style={{ background: colors.revenue }} />
                    本月收入
                  </span>
                  <b>{cny(active.revenueCents)}</b>
                </div>
                <div className="exec-chart-tooltip-row">
                  <span>
                    <i style={{ background: colors.expenses }} />
                    本月支出
                  </span>
                  <b>{cny(active.expensesCents)}</b>
                </div>
                <div className="exec-chart-tooltip-row">
                  <span>
                    <i
                      style={{
                        background: active.profitCents < 0 ? colors.loss : colors.profit,
                      }}
                    />
                    净结余 / 利润
                  </span>
                  <b>{cny(active.profitCents)}</b>
                </div>
                {onSelect && <small className="exec-chart-tooltip-hint">点击柱体下钻明细 →</small>}
              </>
            ) : (
              <div className="exec-chart-tooltip-row">
                <span>状态</span>
                <b>核算中（尚未出已复核报表）</b>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="exec-chart-legend">
        <span>
          <i style={{ background: colors.revenue }} />
          收入
        </span>
        <span>
          <i style={{ background: colors.expenses }} />
          支出
        </span>
        <span>
          <i style={{ background: colors.profit }} />
          净结余（盈利）
        </span>
        <span>
          <i style={{ background: colors.loss }} />
          亏损预警
        </span>
        {onSelect && <span className="exec-chart-note">点击任意子公司柱体可下钻明细</span>}
      </div>
    </div>
  );
}

export function ProgressRing({
  value,
  total,
  label,
}: {
  value: number;
  total: number;
  label: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  const r = 24;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - pct / 100);
  return (
    <div className="exec-progress-ring" role="img" aria-label={`${label} ${pct}%`}>
      <svg viewBox="0 0 60 60">
        <circle cx="30" cy="30" r={r} className="exec-ring-track" />
        <circle
          cx="30"
          cy="30"
          r={r}
          className="exec-ring-bar"
          strokeDasharray={c.toFixed(1)}
          strokeDashoffset={offset.toFixed(1)}
        />
      </svg>
      <span>{pct}%</span>
    </div>
  );
}
