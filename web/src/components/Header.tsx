type HeaderProps = {
  featureName: string;
  onReset: () => void;
};

export function Header({
  featureName,
  onReset,
}: HeaderProps) {
  return (
    <header className="app-header">
      <button
        className="brand-lockup"
        onClick={onReset}
        title="返回工作台"
        type="button"
      >
        <span className="brand-mark" aria-hidden="true">
          <span />
        </span>
        <span className="brand-copy">
          <strong>Praxis AI</strong>
          <span>/</span>
          <small>{featureName}</small>
        </span>
      </button>

    </header>
  );
}
