import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 128;

export function passwordStrength(
  password: string,
  username?: string,
  displayName?: string,
): {
  level: 0 | 1 | 2 | 3;
  label: string;
} {
  if (password.length === 0) return { level: 0, label: "Enter a password" };
  if (password.length < PASSWORD_MIN) return { level: 1, label: "Too short" };

  const lowerPw = password.toLowerCase();
  const trimmedUser = username?.trim().toLowerCase();
  if (trimmedUser && trimmedUser.length >= 3 && lowerPw.includes(trimmedUser)) {
    return { level: 1, label: "Cannot contain username" };
  }

  const trimmedDisplay = displayName?.trim().toLowerCase();
  if (
    trimmedDisplay &&
    trimmedDisplay.length >= 3 &&
    (lowerPw.includes(trimmedDisplay) ||
      lowerPw.includes(trimmedDisplay.replace(/\s+/g, "")))
  ) {
    return { level: 1, label: "Cannot contain display name" };
  }

  if (new Set(lowerPw).size <= 2) {
    return { level: 1, label: "Too simple" };
  }

  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) =>
    r.test(password),
  ).length;
  if (password.length >= 14 && classes >= 3)
    return { level: 3, label: "Strong" };
  return { level: 2, label: "Acceptable" };
}

/** Password input with a Lucide Eye/EyeOff toggle. `ref` is a normal React 19 prop. */
export function PasswordInput({
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "type">) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        type={visible ? "text" : "password"}
        className={cn("pr-10", className)}
        maxLength={PASSWORD_MAX}
        {...props}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        className="fx-transition absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        {visible ? (
          <EyeOff className="size-4" strokeWidth={1.5} />
        ) : (
          <Eye className="size-4" strokeWidth={1.5} />
        )}
      </button>
    </div>
  );
}

/** Live length meter (10..128) with a three-segment strength bar. */
export function PasswordMeter({
  password,
  username,
  displayName,
}: {
  password: string;
  username?: string;
  displayName?: string;
}) {
  const { level, label } = passwordStrength(password, username, displayName);
  const tone =
    level === 3 ? "bg-success" : level === 2 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2" aria-live="polite">
      <div className="flex flex-1 gap-1" aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <span
            key={i}
            className={cn(
              "h-1 flex-1 rounded-full",
              i <= level ? tone : "bg-border",
            )}
          />
        ))}
      </div>
      <span className="text-[11px] font-medium text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "tabular-nums text-[11px]",
          password.length > 0 && password.length < PASSWORD_MIN
            ? "text-danger"
            : "text-muted-foreground",
        )}
      >
        {password.length}/{PASSWORD_MAX}
      </span>
    </div>
  );
}
