// Vite's `?raw` import: a fixture file's text, for tests (vite/client isn't in tsconfig's types).
declare module "*?raw" {
  const text: string;
  export default text;
}
