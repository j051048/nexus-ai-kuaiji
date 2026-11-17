import { formatCents, type FormatCentsOptions } from './money';
import './money-cell.css';

export function MoneyCell({
  cents,
  options,
  className = '',
}: {
  cents: bigint;
  options?: FormatCentsOptions;
  className?: string;
}) {
  return (
    <span
      className={`money-cell${cents < 0n ? ' money-cell-negative' : ''}${className ? ` ${className}` : ''}`}
    >
      {formatCents(cents, options)}
    </span>
  );
}
