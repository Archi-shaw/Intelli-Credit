"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, FileText, Send, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge, Button, Callout, Card, Input } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { label } from "@/lib/format";
import type { ChatMessage } from "@/lib/types";

const SUGGESTIONS = ["What was the revenue in FY 2023-24?", "What differences exist between GST and bank credits?",
  "What are the major financial risk factors?", "Which documents are missing?"];

function Bubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`grid size-8 shrink-0 place-items-center rounded-full ${isUser ? "bg-primary text-white" : "bg-muted"}`}>{isUser ? <User className="size-4" /> : <Bot className="size-4" />}</div>
      <div className={`max-w-[80%] space-y-2 rounded-lg px-4 py-3 text-sm ${isUser ? "bg-primary text-primary-foreground" : "border bg-card"}`}>
        <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
        {!isUser && (
          <>
            <div className="flex flex-wrap gap-1.5">
              <Badge tone={m.grounded ? "success" : "warning"}>{m.grounded ? "Source-supported" : "Not verified"}</Badge>
              <Badge>{m.method === "llm" ? "LLM answer from cited sources" : "Extractive (no LLM)"}</Badge>
            </div>
            {m.citations.length > 0 && (
              <ul className="space-y-1 border-t pt-2 text-xs" aria-label="Sources">
                {m.citations.map((c) => (
                  <li key={c.ref} className="flex gap-2"><span className="font-mono font-semibold text-primary">[{c.ref}]</span>
                    <span className="text-muted-foreground"><FileText className="mr-1 inline size-3" />{c.document_name ? `${c.document_name}${c.page_number ? `, page ${c.page_number}` : ""}` : label(c.type)}{c.period ? ` · ${c.period}` : ""}</span></li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function AssistantTab({ appId }: { appId: number }) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [q, setQ] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const sessions = useQuery({ queryKey: ["chat-sessions", appId], queryFn: () => api.get<{ items: { id: number; title: string }[] }>(`/assessments/${appId}/chat/sessions`) });
  const status = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<{ llm: { status: string; provider: string } }>("/system/integrations"), staleTime: 300_000 });
  const ask = useMutation({
    mutationFn: (question: string) => api.post<{ session_id: number; question: ChatMessage; answer: ChatMessage }>(`/assessments/${appId}/chat`, { question, session_id: sessionId ?? undefined }),
    onSuccess: (r) => { setSessionId(r.session_id); setMessages((m) => [...m, r.question, r.answer]); sessions.refetch(); },
  });
  const load = async (id: number) => {
    const s = await api.get<{ messages: ChatMessage[] }>(`/chat/sessions/${id}`);
    setSessionId(id);
    setMessages(s.messages);
  };
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, ask.isPending]);
  const submit = (text: string) => { const t = text.trim(); if (t.length >= 3 && !ask.isPending) { ask.mutate(t); setQ(""); } };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_260px]">
      <Card className="flex h-[640px] flex-col">
        <div className="border-b px-5 py-3">
          <h3 className="text-sm font-semibold">Document assistant</h3>
          <p className="text-xs text-muted-foreground">Answers only from this assessment&apos;s documents and recorded analysis, with citations. Unsupported questions are reported as unverifiable.</p>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {status.data && status.data.llm.status !== "configured" && <Callout>No language model is configured, so answers are extractive: the matching recorded figures, findings and passages with their sources.</Callout>}
          {messages.length === 0 && (
            <div className="space-y-2"><p className="text-sm text-muted-foreground">Try asking:</p>
              <div className="flex flex-wrap gap-2">{SUGGESTIONS.map((s) => <Button key={s} size="sm" variant="outline" onClick={() => submit(s)}>{s}</Button>)}</div></div>
          )}
          {messages.map((m) => <Bubble key={m.id} m={m} />)}
          {ask.isPending && <p className="text-sm text-muted-foreground">Searching the documents…</p>}
          {ask.error && <Callout tone="danger">{errorMessage(ask.error)}</Callout>}
          <div ref={end} />
        </div>
        <form className="flex gap-2 border-t p-4" onSubmit={(e) => { e.preventDefault(); submit(q); }}>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about revenue, discrepancies, risks or missing documents…" aria-label="Question" maxLength={1000} />
          <Button type="submit" disabled={q.trim().length < 3} loading={ask.isPending}><Send />Ask</Button>
        </form>
      </Card>
      <Card className="h-fit">
        <div className="flex items-center justify-between border-b px-4 py-3"><h3 className="text-sm font-semibold">Conversations</h3><Button size="sm" variant="ghost" onClick={() => { setSessionId(null); setMessages([]); }}>New</Button></div>
        <ul className="max-h-[560px] overflow-y-auto">
          {sessions.data?.items.length === 0 && <li className="px-4 py-3 text-xs text-muted-foreground">No conversations yet.</li>}
          {sessions.data?.items.map((s) => <li key={s.id}><button onClick={() => load(s.id)} className={`w-full truncate px-4 py-2 text-left text-sm hover:bg-muted ${s.id === sessionId ? "bg-muted font-medium" : ""}`}>{s.title}</button></li>)}
        </ul>
      </Card>
    </div>
  );
}
