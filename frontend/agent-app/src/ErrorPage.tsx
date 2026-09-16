export function ErrorPage({ code }: { code: number }) {
  const title =
    code === 404 ? "Page Not Found" : code === 403 ? "Forbidden" : "Server Error";
  const lead =
    code === 404
      ? "Sorry, the page you are looking for does not exist."
      : code === 403
        ? "Sorry, you do not have permission to access this resource."
        : "Sorry, something went wrong on our end. Please try again later.";

  return (
    <div className="aa-root aa-auth-screen" data-page="error">
      <div className="aa-error-card">
        <div className="aa-error-code">{code}</div>
        <h2 className="aa-error-title">{title}</h2>
        <p className="aa-error-lead">{lead}</p>
        <a className="aa-btn aa-btn-primary" href="/">
          Return to Home
        </a>
      </div>
    </div>
  );
}
