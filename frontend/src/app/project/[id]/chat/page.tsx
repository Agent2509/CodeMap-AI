import ChatReadingView from '@/components/ChatReadingView';

export default async function ProjectChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ChatReadingView projectId={id} />;
}
