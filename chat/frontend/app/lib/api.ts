const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: string;
  avatar_url: string | null;
  is_active: boolean;
}

export interface Channel {
  id: number;
  name: string;
  description: string;
  project_id: string | null;
  created_by: number;
  is_archived: number;
  member_count: number;
  created_at: string;
}

export interface Attachment {
  id: number;
  file_name: string;
  file_path: string;
  file_size: number;
  mime_type: string;
}

export interface Message {
  id: number;
  channel_id: number;
  user_id: number;
  content: string;
  parent_id: number | null;
  created_at: string;
  display_name: string;
  avatar_url: string | null;
  attachments: Attachment[];
}

export interface AttendanceRecord {
  id: number;
  user_id: number;
  display_name: string;
  record_date: string;
  clock_in: string | null;
  clock_out: string | null;
  location_in: string | null;
  location_out: string | null;
  note: string;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "API Error");
  }
  return res.json();
}

// Users
export const fetchUsers = () => apiFetch<User[]>("/api/users");

// Channels
export const fetchChannels = () => apiFetch<Channel[]>("/api/channels");

// Messages
export const fetchMessages = (channelId: number) =>
  apiFetch<Message[]>(`/api/channels/${channelId}/messages`);

export const sendMessage = (channelId: number, userId: number, content: string) =>
  apiFetch<Message>(`/api/channels/${channelId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, content }),
  });

export async function sendMessageWithAttachment(
  channelId: number,
  userId: number,
  content: string,
  file: File
): Promise<Message> {
  const form = new FormData();
  form.append("user_id", String(userId));
  form.append("content", content);
  form.append("file", file);
  const res = await fetch(
    `${API_BASE}/api/channels/${channelId}/messages/with-attachment`,
    { method: "POST", body: form }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Upload Error");
  }
  return res.json();
}

// Attendance
export const fetchTodayAttendance = () =>
  apiFetch<AttendanceRecord[]>("/api/attendance/today");

export async function clockIn(userId: number, location: string = "") {
  const form = new FormData();
  form.append("user_id", String(userId));
  form.append("location", location);
  return apiFetch<{ message: string; time: string; date: string }>(
    "/api/attendance/clock-in",
    { method: "POST", body: form }
  );
}

export async function clockOut(userId: number, location: string = "") {
  const form = new FormData();
  form.append("user_id", String(userId));
  form.append("location", location);
  return apiFetch<{ message: string; time: string; date: string }>(
    "/api/attendance/clock-out",
    { method: "POST", body: form }
  );
}

export function attachmentUrl(path: string): string {
  return `${API_BASE}${path}`;
}
