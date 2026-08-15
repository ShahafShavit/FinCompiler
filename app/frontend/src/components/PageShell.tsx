import type { ReactNode } from 'react';

type PageWidth = 'narrow' | 'default' | 'wide' | 'full';

type PageShellProps = {
  title: string;
  sub?: string;
  width?: PageWidth;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export default function PageShell({
  title,
  sub,
  width = 'default',
  actions,
  children,
  className = '',
}: PageShellProps) {
  return (
    <div className={`page page--${width}${className ? ` ${className}` : ''}`}>
      <header className="page-header">
        <div className="page-header__text">
          <h1>{title}</h1>
          {sub ? <p className="sub">{sub}</p> : null}
        </div>
        {actions ? <div className="page-header__actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}
