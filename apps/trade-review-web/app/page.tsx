import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-3xl font-bold text-slate-800">Trade Review System</h1>
      <p className="text-slate-600">Trade analytics and review dashboard</p>
      <Link
        href="/review"
        className="rounded-lg bg-blue-600 px-6 py-3 text-white font-medium hover:bg-blue-700 transition-colors"
      >
        Go to Review
      </Link>
    </main>
  );
}