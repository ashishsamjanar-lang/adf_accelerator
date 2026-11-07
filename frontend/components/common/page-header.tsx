import type { ReactNode } from "react";

interface PageHeaderProps {
  eyebrow: string;
  title: string;
  detail: string;
  action?: ReactNode;
}

export function PageHeader({ eyebrow, title, detail, action }: PageHeaderProps) {
  return (
    <div className="page-heading">
      <div>
        <span className="eyebrow accent">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      {action}
    </div>
  );
}