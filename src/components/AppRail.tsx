import type { ComponentType, SVGProps } from "react";
import type { Profile, Tab } from "../types";
import { AccountMenu } from "./AccountMenu";
import { HomeIcon, LogInIcon, SlidersIcon, TerminalIcon } from "./icons";
import { cx } from "../lib/utils";

const items: Array<{ key: Tab; label: string; icon: ComponentType<SVGProps<SVGSVGElement>> }> = [
  { key: "home", label: "Dashboard", icon: HomeIcon },
  { key: "settings", label: "Presence and platform", icon: SlidersIcon },
  { key: "logs", label: "Live log", icon: TerminalIcon },
];

export function AppRail({
  tab,
  profile,
  running,
  onTab,
  onSwitchAccount,
}: {
  tab: Tab;
  profile: Profile | null;
  running: boolean;
  onTab: (tab: Tab) => void;
  onSwitchAccount: () => void;
}) {
  return (
    <nav
      className="flex w-14 shrink-0 flex-col items-center gap-3 px-1 pb-2 pt-1"
      aria-label="Primary"
    >
      <div className="flex w-full flex-1 flex-col items-center gap-1">
        {items.map((item) => {
          const Icon = item.icon;
          const active = tab === item.key;
          return (
            <button
              key={item.key}
              type="button"
              className={cx("rail-button", active && "rail-button-active")}
              title={item.label}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              onClick={() => onTab(item.key)}
            >
              <Icon />
            </button>
          );
        })}
      </div>

      <span className="my-1 h-px w-6 rounded-full bg-border" aria-hidden="true" />

      <div className="pb-1">
        {profile ? (
          <AccountMenu profile={profile} running={running} onSwitchAccount={onSwitchAccount} />
        ) : (
          <button
            type="button"
            className="rail-button"
            title="Sign in"
            aria-label="Sign in"
            onClick={() => onTab("home")}
          >
            <LogInIcon />
          </button>
        )}
      </div>
    </nav>
  );
}
