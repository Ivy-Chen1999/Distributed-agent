// Small primitives reproducing the design's inline styles, including style-hover / style-focus.
import { useState, type ButtonHTMLAttributes, type CSSProperties, type InputHTMLAttributes, type ReactNode } from 'react';
import { CARD, FONT, RED } from '../design';

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { hover?: CSSProperties };

/** <button> with the design's `style-hover` behaviour. */
export function HButton({ style, hover, onMouseEnter, onMouseLeave, type, ...rest }: BtnProps) {
  const [on, setOn] = useState(false);
  return (
    <button
      type={type ?? 'button'}
      {...rest}
      style={on && hover && !rest.disabled ? { ...style, ...hover } : style}
      onMouseEnter={(e) => {
        setOn(true);
        onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        setOn(false);
        onMouseLeave?.(e);
      }}
    />
  );
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & { focusStyle?: CSSProperties };

/** <input> with the design's `style-focus` behaviour. */
export function HInput({ style, focusStyle, onFocus, onBlur, ...rest }: InputProps) {
  const [on, setOn] = useState(false);
  return (
    <input
      {...rest}
      style={on && focusStyle ? { ...style, ...focusStyle } : style}
      onFocus={(e) => {
        setOn(true);
        onFocus?.(e);
      }}
      onBlur={(e) => {
        setOn(false);
        onBlur?.(e);
      }}
    />
  );
}

export const Dot = ({ c, size = 7, radius = '50%', anim, style }: { c: string; size?: number; radius?: string | number; anim?: string; style?: CSSProperties }) => (
  <span style={{ width: size, height: size, borderRadius: radius, background: c, flex: 'none', animation: anim ?? 'none', ...style }} />
);


export const Arrow = ({ w = 20, color = 'var(--n2)' }: { w?: number; color?: string }) => (
  <svg width={w} height="10" viewBox="0 0 20 10" fill="none" stroke={color} strokeWidth="1.6">
    <path d="M0 5h17M13 1l4 4-4 4" />
  </svg>
);

export const Chevron = ({ d = 'M9 6l6 6-6 6', size = 16, style }: { d?: string; size?: number; style?: CSSProperties }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={style}>
    <path d={d} />
  </svg>
);

/** Segmented control (Group by, backend per role, router mode, replay speed). */
export function Seg<T extends string>({
  options,
  value,
  onChange,
  pad = '6px 12px',
  font = `700 12px ${FONT}`,
  variant = 'card',
  style,
}: {
  options: { v: T; label: string }[];
  value: T | null;
  onChange: (v: T) => void;
  pad?: string;
  font?: string;
  variant?: 'card' | 'ink';
  style?: CSSProperties;
}) {
  return (
    <div style={{ display: 'flex', background: 'var(--soft)', borderRadius: 4, padding: 3, ...style }}>
      {options.map((o) => {
        const on = o.v === value;
        const bg = on ? (variant === 'ink' ? 'var(--ink)' : 'var(--card)') : 'transparent';
        const col = variant === 'ink' ? (on ? 'var(--card)' : 'var(--n1)') : 'var(--ink)';
        return (
          <button
            key={o.v}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.v)}
            style={{ border: 'none', borderRadius: 3, padding: pad, whiteSpace: 'nowrap', font, fontVariantNumeric: 'tabular-nums', background: bg, color: col, cursor: 'pointer' }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export const OUTLINE_BTN: CSSProperties = {
  background: 'transparent',
  color: 'var(--ink)',
  border: '1px solid var(--ink)',
  borderRadius: 4,
  padding: '8px 12px',
  font: `700 12px ${FONT}`,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

export const PRIMARY_BTN: CSSProperties = {
  flex: 'none',
  whiteSpace: 'nowrap',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 8,
  background: '#00C4CC',
  color: '#0F0F0F',
  border: 'none',
  borderRadius: 4,
  padding: '9px 16px',
  font: `700 13px ${FONT}`,
  letterSpacing: '.02em',
  cursor: 'pointer',
};

/** Loading skeleton in the style of "Dossier is assembling". */
export function LoadingCard({ title = 'Loading', sub }: { title?: string; sub?: string }) {
  return (
    <div role="status" style={{ ...CARD, border: '1px dashed var(--line)', padding: '40px 24px', textAlign: 'center' }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
      {sub && <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 6 }}>{sub}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520, margin: '18px auto 0' }}>
        <div style={{ height: 14, borderRadius: 3, background: 'var(--soft)' }} />
        <div style={{ height: 14, width: '82%', borderRadius: 3, background: 'var(--soft)' }} />
        <div style={{ height: 14, width: '64%', borderRadius: 3, background: 'var(--soft)' }} />
      </div>
    </div>
  );
}

/** Empty state in the style of "Board is empty". */
export function EmptyCard({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <div style={{ ...CARD, padding: '40px 24px', textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 700 }}>{title}</div>
      {sub && <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 4 }}>{sub}</div>}
      {action && <div style={{ marginTop: 16, display: 'flex', justifyContent: 'center' }}>{action}</div>}
    </div>
  );
}

/** Error state in the style of the failed-expert row. */
export function ErrorCard({ title = 'Something went wrong', message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
  return (
    <div role="alert" style={{ ...CARD, padding: '16px 18px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', borderColor: RED }}>
      <Dot c={RED} size={8} />
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>{title}</div>
        <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 2, lineHeight: 1.5, wordBreak: 'break-word' }}>{message}</div>
      </div>
      {onRetry && (
        <button type="button" onClick={onRetry} style={OUTLINE_BTN}>
          Retry
        </button>
      )}
    </div>
  );
}
