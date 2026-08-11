import { ReactNode } from "react";

/** Ported verbatim from DUT_browser's `src/components/shell/Card.tsx`
 *  (the `EmptyState` and `KpiCard` exports are cut — M1 has no KPI row). */

type CardProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
};

export function Card({ title, subtitle, actions, className, children }: CardProps) {
  return (
    <section className={`card${className ? ` ${className}` : ""}`}>
      <div className="card-head">
        <div className="card-titles">
          <div className="card-title">{title}</div>
          {subtitle ? <div className="card-sub">{subtitle}</div> : null}
        </div>
        {actions ? <div className="card-actions">{actions}</div> : null}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}
