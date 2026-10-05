// The console's building blocks, Swiss style: one typeface, hierarchy by size and weight,
// thin rules instead of boxes, left-aligned, red only for what matters (styles/theme.css).
import { cn } from '@/lib/utils';

export function PageHeader({ title, lead, children }: { title: string; lead?: string; children?: React.ReactNode }) {
  return (
    <header className="space-y-2 pb-8">
      <h1 className="text-4xl font-bold tracking-tight">{title}</h1>
      {lead && <p className="text-muted-foreground max-w-prose text-base">{lead}</p>}
      {children}
    </header>
  );
}

/** A numbered section: "01  Conversation", a full-width rule above. */
export function SectionHeading({ n, title, id }: { n: number; title: string; id?: string }) {
  return (
    <h2 id={id} className="border-foreground flex scroll-mt-6 items-baseline gap-4 border-t pt-4 text-xl font-bold tracking-tight">
      <span className="text-muted-foreground w-8 text-sm font-medium tabular-nums">{String(n).padStart(2, '0')}</span>
      {title}
    </h2>
  );
}

export function Label({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('block text-sm font-medium', className)}>{children}</span>;
}

/** A number that matters, and what it means. */
export function KeyStat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <p className="text-signal text-3xl font-bold tracking-tight tabular-nums">{value}</p>
      <p className="text-muted-foreground text-sm">{label}</p>
    </div>
  );
}

export const inputClass =
  'border-foreground/30 bg-background w-full rounded-sm border px-3 py-2 text-sm focus:border-foreground focus:outline-none';

export const buttonClass =
  'bg-foreground text-background rounded-sm px-5 py-2.5 text-sm font-medium disabled:opacity-50';
export const secondaryButtonClass =
  'border-foreground/30 hover:border-foreground rounded-sm border px-4 py-2 text-sm font-medium disabled:opacity-50';

/** A count: big and black (red is for results that matter, KeyStat). */
export function Stat({ value, label }: { value: number | string; label: string }) {
  return (
    <div>
      <p className="text-4xl font-bold tracking-tight tabular-nums">{value}</p>
      <p className="text-muted-foreground text-sm">{label}</p>
    </div>
  );
}

export const textActionClass = 'text-sm underline-offset-4 hover:underline disabled:opacity-50';
