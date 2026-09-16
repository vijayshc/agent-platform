import { adminGet } from "../adminShared";

export interface SeriesPoint {
  date: string;
  count: number;
}

export interface BreakdownItem {
  name: string;
  value: number;
}

export interface RecentRun {
  id: number;
  agent_slug: string | null;
  task: string | null;
  status: string;
  started_at: string | null;
}

export interface DashboardMetrics {
  users: number;
  roles: number;
  agents: number;
  publishedAgents: number;
  skills: number;
  mcpServers: number;
  mcpServersStdio: number;
  hostedApps: number;
  hostedAppsRunning: number;
  runs: number;
  runsToday: number;
  conversations: number;
  interactions: number;
  interactionsToday: number;
}

export interface DashboardBreakdowns {
  runsByStatus: BreakdownItem[];
  runsByAgent: BreakdownItem[];
  messagesByRole: BreakdownItem[];
  topUsers: BreakdownItem[];
}

export interface DashboardAnalytics {
  metrics: DashboardMetrics;
  series: {
    runs: SeriesPoint[];
    messages: SeriesPoint[];
  };
  breakdowns: DashboardBreakdowns;
  recentRuns: RecentRun[];
  days: number;
}

export function fetchDashboardAnalytics(days = 14): Promise<DashboardAnalytics> {
  return adminGet<DashboardAnalytics>(`/admin/api/dashboard/analytics?days=${days}`);
}
