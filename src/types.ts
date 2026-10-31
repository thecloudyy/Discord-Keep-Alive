export type Config = Record<string, string>;

export type Profile = {
  id?: string;
  username?: string;
  global_name?: string | null;
  avatar_url?: string;
};

export type DetectedAccount = Profile & { client: string; token: string };

export type ScanReport = { accounts: DetectedAccount[]; notes: string[] };

export type Session = { client: string; status: string; os?: string };

export type BotStatus = {
  running: boolean;
  pid?: number | null;
  user?: Profile | null;
  sessions?: Session[];
  spoofed_platform?: string | null;
  updated_at?: number | null;
};

export type Tab = "home" | "settings" | "logs";
