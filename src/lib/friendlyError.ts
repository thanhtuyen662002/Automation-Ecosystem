/**
 * Maps backend error codes and raw messages to human-readable strings.
 * Used across the UI to display user-friendly error explanations.
 */

export type UserFacingError = {
  title: string;
  description: string;
  action?: string; // CTA text
  actionRoute?: string; // CTA route (e.g. "/accounts")
};

const ERROR_MAP: Record<string, UserFacingError> = {
  ACCOUNT_AUTH_REQUIRED: {
    title: "Session expired",
    description: "Your account session has expired. Reconnect your account to continue publishing.",
    action: "Reconnect account",
    actionRoute: "/accounts",
  },
  ACCOUNT_BANNED: {
    title: "Account banned",
    description: "This account has been banned from publishing by the platform.",
    action: "View accounts",
    actionRoute: "/accounts",
  },
  ACCOUNT_LIMITED: {
    title: "Too many posts today",
    description: "This account has reached its daily posting limit. Try again later.",
    action: "View posting limits",
    actionRoute: "/posting-limits",
  },
  MISSING_VIDEO_PATH: {
    title: "Video not found",
    description: "The video file could not be found. Check your content library.",
    action: "Go to content",
    actionRoute: "/content",
  },
  ARTIFACT_NOT_APPROVED: {
    title: "Content not approved",
    description: "This video hasn't been approved yet. Approve it in your content library first.",
    action: "Review content",
    actionRoute: "/content",
  },
  POLICY_VIOLATION: {
    title: "Daily limit reached",
    description: "You've reached your daily posting limit for this platform. Try again tomorrow.",
    action: "View posting limits",
    actionRoute: "/posting-limits",
  },
  RATE_LIMITED: {
    title: "Rate limited",
    description: "Too many requests in a short time. Please wait a few minutes.",
  },
};

/** Convert a backend error_type / error_message into a user-facing message. */
export function friendlyError(
  errorType: string | null | undefined,
  errorMessage: string | null | undefined,
): UserFacingError {
  if (errorType) {
    const mapped = ERROR_MAP[errorType];
    if (mapped) return mapped;

    // Fuzzy match: check if error message contains a known code
    for (const [code, msg] of Object.entries(ERROR_MAP)) {
      if (errorMessage?.includes(code)) return msg;
    }
  }

  // Generic fallback
  return {
    title: "Something went wrong",
    description: errorMessage ?? "An unexpected error occurred. Please try again.",
  };
}
