import { useEffect, useRef } from "react";

// extraRef: an optional second ref (e.g. a portaled menu) that also counts
// as "inside" — without it, a click inside a portal (not a DOM descendant of
// ref) would be treated as an outside click and immediately close the menu.
export function useClickOutside(onOutside, extraRef) {
  const ref = useRef(null);
  useEffect(() => {
    const handler = (e) => {
      const insideMain = ref.current && ref.current.contains(e.target);
      const insideExtra = extraRef && extraRef.current && extraRef.current.contains(e.target);
      if (!insideMain && !insideExtra) onOutside();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onOutside, extraRef]);
  return ref;
}
