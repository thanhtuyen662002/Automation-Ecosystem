Deno.serve((_req) => {
  return new Response(
    JSON.stringify({
      error: "deprecated",
      message: "license-auth is deprecated. Use license-api."
    }),
    {
      status: 410,
      headers: {
        "Content-Type": "application/json"
      }
    }
  );
});
