import Dashboard from "@/components/Dashboard";
import { loadDashboardData } from "@/lib/loadDashboardData";

export default function Home() {
  const data = loadDashboardData();
  return <Dashboard data={data} />;
}
