import coreWebVitals from 'eslint-config-next/core-web-vitals';

const config = [
  ...coreWebVitals,
  { ignores: ['.next/**', 'next-env.d.ts'] },
  {
    // ponytail: React Compiler rules (react-hooks v7) flag 15 pre-existing spots;
    // we don't run the compiler, so warn for now and promote to error once fixed.
    rules: {
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/immutability': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/refs': 'warn',
    },
  },
];

export default config;
