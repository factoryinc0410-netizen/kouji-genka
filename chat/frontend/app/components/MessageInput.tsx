"use client";

import { useRef, useState } from "react";

interface MessageInputProps {
  onSend: (content: string) => void;
  onSendWithFile: (content: string, file: File) => void;
  isMobile: boolean;
  channelName: string;
}

export default function MessageInput({
  onSend,
  onSendWithFile,
  isMobile,
  channelName,
}: MessageInputProps) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    const url = URL.createObjectURL(f);
    setPreview(url);
  };

  const removeFile = () => {
    setFile(null);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const handleSubmit = async () => {
    if (sending) return;
    if (!text.trim() && !file) return;
    setSending(true);
    try {
      if (file) {
        await onSendWithFile(text, file);
      } else {
        await onSend(text);
      }
      setText("");
      removeFile();
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="border-t border-[var(--gray-200)] bg-white px-4 py-3">
      {/* プレビュー */}
      {preview && (
        <div className="mb-2 relative inline-block">
          <img
            src={preview}
            alt="添付プレビュー"
            className="h-20 rounded-lg border border-[var(--gray-200)]"
          />
          <button
            onClick={removeFile}
            className="absolute -top-2 -right-2 w-6 h-6 bg-[var(--danger)] text-white rounded-full text-xs flex items-center justify-center hover:bg-red-600"
          >
            ✕
          </button>
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* ファイル添付ボタン */}
        <button
          onClick={() => fileRef.current?.click()}
          className={`flex-shrink-0 rounded-lg border border-[var(--gray-200)] hover:bg-[var(--gray-100)] transition-colors flex items-center justify-center ${
            isMobile ? "w-12 h-12 text-xl" : "w-10 h-10 text-lg"
          }`}
          title="写真を添付"
        >
          📷
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture={isMobile ? "environment" : undefined}
          onChange={handleFileChange}
          className="hidden"
        />

        {/* テキスト入力 */}
        <textarea
          ref={textRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`#${channelName} にメッセージを送信`}
          rows={1}
          className={`flex-1 resize-none border border-[var(--gray-200)] rounded-lg px-3 focus:outline-none focus:border-[var(--info)] focus:ring-1 focus:ring-[var(--info)] ${
            isMobile ? "py-3 text-base" : "py-2 text-sm"
          }`}
        />

        {/* 送信ボタン */}
        <button
          onClick={handleSubmit}
          disabled={sending || (!text.trim() && !file)}
          className={`flex-shrink-0 rounded-lg font-bold text-[var(--navy)] transition-colors flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed ${
            isMobile
              ? "w-12 h-12 text-xl bg-[var(--accent)] hover:bg-[var(--accent-hover)]"
              : "w-10 h-10 text-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)]"
          }`}
          title="送信"
        >
          {sending ? "…" : "➤"}
        </button>
      </div>

      {!isMobile && (
        <p className="text-xs text-[var(--gray-400)] mt-1">
          Enter で送信 / Shift+Enter で改行
        </p>
      )}
    </div>
  );
}
