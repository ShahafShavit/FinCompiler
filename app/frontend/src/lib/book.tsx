import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';

import { getJson, getActiveBookId, setActiveBookId } from './api';

export type BookInfo = {
  id: string;
  label: string;
  fetcher: string | null;
  backfill: boolean;
};

type BookState = {
  bookId: string;
  books: BookInfo[];
  setBookId: (id: string) => void;
};

const FALLBACK: BookInfo[] = [
  { id: 'personal', label: 'אישי', fetcher: null, backfill: false },
  { id: 'shared', label: 'One Zero', fetcher: 'onezero', backfill: true },
];

const BookContext = createContext<BookState | null>(null);

export function BookProvider({ children }: { children: ReactNode }) {
  const [bookId, setBookIdState] = useState(() => {
    const saved = getActiveBookId();
    return saved || 'personal';
  });
  const [books, setBooks] = useState<BookInfo[]>(FALLBACK);

  const setBookId = useCallback((id: string) => {
    setActiveBookId(id);
    setBookIdState(id);
  }, []);

  useEffect(() => {
    setActiveBookId(bookId);
  }, [bookId]);

  useEffect(() => {
    let cancel = false;
    void getJson<{ books?: BookInfo[] }>('/api/books')
      .then((data) => {
        if (!cancel && Array.isArray(data.books) && data.books.length) setBooks(data.books);
      })
      .catch(() => {
        /* keep fallback labels */
      });
    return () => {
      cancel = true;
    };
  }, [bookId]);

  return (
    <BookContext.Provider value={{ bookId, books, setBookId }}>
      {children}
    </BookContext.Provider>
  );
}

export function useBook(): BookState {
  const value = useContext(BookContext);
  if (!value) throw new Error('BookProvider is missing');
  return value;
}
