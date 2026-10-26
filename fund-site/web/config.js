// Public settings. The anon key is meant to be public: row-level security and the
// database functions decide what each signed-in member can see or change.
window.FUND_CONFIG = {
  supabaseUrl: 'https://bmudotiuafxrhzkqihyb.supabase.co',
  supabaseAnonKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtdWRvdGl1YWZ4cmh6a3FpaHliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI5NjcxNDcsImV4cCI6MjEwODU0MzE0N30.xipuPyy0y78Jc3CrR5OmncD7FWT0NsPgiroorQRpnAA',
  emailDomain: 'members.midaskiyoshi.fund',
  celoRpc: 'https://forno.celo.org',
  // Stablecoins counted in the pool wallet's on-chain balance (Celo mainnet)
  stables: [
    { symbol: 'USDT', address: '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e', decimals: 6 },
    { symbol: 'USDC', address: '0xcebA9300f2b948710d2653dD7B07f33A8B32118C', decimals: 6 },
  ],
}
