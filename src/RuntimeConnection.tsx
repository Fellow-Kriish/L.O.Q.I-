import type { ReactNode } from 'react'
import useRuntime from './useRuntime'

type Props = { children: (runtime: ReturnType<typeof useRuntime>) => ReactNode }

export default function RuntimeConnection({ children }: Props) {
  const runtime = useRuntime()
  return children(runtime)
}
