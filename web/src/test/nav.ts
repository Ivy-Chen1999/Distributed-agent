// Open a screen by its sidebar name, switching to the other interface (the law / the system)
// first when the current one does not list it, as a user would.
import { fireEvent, screen, within } from '@testing-library/react';

export function goTo(name: string): void {
  const nav = () => screen.getByRole('navigation', { name: 'Screens' });
  if (!within(nav()).queryByText(name)) {
    const other = within(screen.getByRole('group', { name: 'Interface' }))
      .getAllByRole('button')
      .find((b) => b.getAttribute('aria-pressed') === 'false');
    if (other) fireEvent.click(other);
  }
  fireEvent.click(within(nav()).getByText(name));
}
