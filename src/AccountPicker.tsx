import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { accountChoices, nearestChoiceIndex } from './account-picker';

interface Props {
  accounts: { code: string; name: string }[];
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  ariaLabel: string;
}

export function AccountPicker({ accounts, value, onChange, disabled = false, ariaLabel }: Props) {
  const selected = accounts.find((account) => account.code === value);
  const [query, setQuery] = useState(selected ? `${selected.code} · ${selected.name}` : '');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [menu, setMenu] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const choices = useMemo(() => accountChoices(accounts, query), [accounts, query]);

  useEffect(() => {
    if (!open) setQuery(selected ? `${selected.code} · ${selected.name}` : '');
  }, [open, selected?.code, selected?.name]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = inputRef.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom;
      const height = Math.min(260, Math.max(120, below - 12));
      const width = Math.min(Math.max(rect.width, 280), window.innerWidth - 16);
      setMenu({
        top: below < 160 ? Math.max(8, rect.top - height - 4) : rect.bottom + 4,
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        width,
        height,
      });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open]);

  const choose = (code: string) => {
    const account = accounts.find((item) => item.code === code);
    onChange(code);
    setQuery(account ? `${account.code} · ${account.name}` : '');
    setOpen(false);
    setActive(-1);
    inputRef.current?.focus();
  };

  return (
    <div className="account-picker">
      <input
        ref={inputRef}
        role="combobox"
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={
          open && active >= 0 && choices[active] ? `${listId}-${choices[active].code}` : undefined
        }
        autoComplete="off"
        disabled={disabled}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={(event) => {
          event.currentTarget.select();
          setQuery('');
          setOpen(true);
          setActive(accountChoices(accounts, '').findIndex((choice) => choice.code === value));
        }}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setOpen(false);
            setQuery(selected ? `${selected.code} · ${selected.name}` : '');
            return;
          }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
            setActive((current) =>
              nearestChoiceIndex(choices, current, event.key === 'ArrowDown' ? 1 : -1),
            );
            return;
          }
          if (event.key === 'Enter' && open) {
            event.preventDefault();
            const choice = choices[active];
            if (choice?.selectable) choose(choice.code);
            else if (choices.length === 1 && choices[0].selectable) choose(choices[0].code);
          }
        }}
      />
      {open && (
        <ul
          className="account-picker-list"
          id={listId}
          role="listbox"
          aria-label={`${ariaLabel}候选`}
          style={{
            top: menu?.top,
            left: menu?.left,
            width: menu?.width,
            maxHeight: menu?.height,
          }}
        >
          {choices.length === 0 && (
            <li className="account-picker-empty" role="presentation">
              没有匹配的末级科目
            </li>
          )}
          {choices.map((choice, index) => {
            const selectedRow = choice.code === value;
            return (
              <li
                key={choice.code}
                id={`${listId}-${choice.code}`}
                role="option"
                aria-selected={selectedRow}
                aria-disabled={!choice.selectable}
                className={[
                  'account-picker-option',
                  !choice.selectable ? 'is-group' : '',
                  index === active ? 'is-active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => choice.selectable && setActive(index)}
                onClick={() => choice.selectable && choose(choice.code)}
              >
                <span className="account-picker-code">{choice.code}</span>
                <span>{choice.name}</span>
                {!choice.selectable && <span className="account-picker-tag">分组</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
