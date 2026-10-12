import Workspace from '@/components/Workspace';

type Search = { [key: string]: string | string[] | undefined };

const first = (value: string | string[] | undefined) =>
  Array.isArray(value) ? value[0] : value;

export default async function ProjectPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Search>;
}) {
  const { id } = await params;
  const query = await searchParams;

  const line = first(query.line);

  return (
    <Workspace
      // Remount when switching projects so no stale graph survives.
      key={id}
      projectId={id}
      initialFile={first(query.file)}
      initialLine={line ? Number(line) : undefined}
      task={first(query.task)}
    />
  );
}
