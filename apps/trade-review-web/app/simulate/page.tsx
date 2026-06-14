// app/simulate/page.tsx — Phase 15.3: Simulation page
import Link from 'next/link';
import SimulationConfigPanel from '@/components/simulation/SimulationConfigPanel';

export const dynamic = 'force-dynamic';

type Props = {
  searchParams: Promise<{ symbol?: string }>;
};

export default async function SimulatePage({ searchParams }: Props) {
  const params = await searchParams;
  const initialSymbol = params.symbol ?? 'XAUUSDm';

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="mx-auto max-w-7xl">
          <div className="flex items-center gap-4">
            <Link
              href="/review"
              className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
            >
              ← Back to Review
            </Link>
            <div>
              <h1 className="text-2xl font-bold text-slate-800">Trade Simulation</h1>
              <p className="mt-0.5 text-sm text-slate-500">
                Parameter sweep — SL distance × R:R ratio
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        <SimulationConfigPanel initialSymbol={initialSymbol} />
      </main>
    </div>
  );
}