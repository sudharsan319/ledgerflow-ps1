import { useId } from 'react';
import { Search, X } from 'lucide-react';

type Props = {
  query: string;
  onQueryChange: (query: string) => void;
  label: string;
  placeholder?: string;
  matched: number;
  total: number;
  hint?: string;
  disabled?: boolean;
};

export function ListSearch({ query, onQueryChange, label, placeholder, matched, total, hint, disabled = false }: Props) {
  const id = useId();
  return <div className="list-search">
    <div className="search-field">
      <Search size={16} aria-hidden="true" />
      <input type="search" aria-label={label} aria-describedby={`${id}-count${hint ? ` ${id}-hint` : ''}`} placeholder={placeholder || label + '…'} value={query} onChange={event => onQueryChange(event.target.value)} disabled={disabled} autoComplete="off" spellCheck={false} />
      {query && <button type="button" disabled={disabled} onClick={() => onQueryChange('')} aria-label={`Clear ${label.toLowerCase()}`} title="Clear search"><X size={15} aria-hidden="true" /></button>}
    </div>
    <span id={`${id}-count`} className="list-search-count" role="status" aria-live="polite" aria-atomic="true">{matched} of {total} {query.trim() ? 'matched' : 'shown'}</span>
    {hint && <p id={`${id}-hint`} className="list-search-hint">{hint}</p>}
  </div>;
}
