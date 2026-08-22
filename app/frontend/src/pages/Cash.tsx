import PageShell from '../components/PageShell';
import CashRegister from '../components/CashRegister';
import { he } from '../lib/he';

import './Cash.css';

export default function Cash() {
  return (
    <PageShell title={he.cash.title} sub={he.cash.sub} width="default" className="cash-page">
      <CashRegister />
    </PageShell>
  );
}
