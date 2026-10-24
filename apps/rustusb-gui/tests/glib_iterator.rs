//! Exercise the scoped GLib fix through the actual GUI dependency graph.
#![forbid(unsafe_code)]
#[cfg(test)]
mod tests {
    use glib::prelude::*;
    use std::error::Error;
    type TestResult = Result<(), Box<dyn Error>>;
    #[test]
    fn forward_and_borrow_after_iterator_drop() -> TestResult {
        let variant = ["alpha", "βήτα", "", "終"].to_variant();
        let first = {
            let mut iter = variant.array_iter_str()?;
            assert_eq!(iter.size_hint(), (4, Some(4)));
            let first = iter.next();
            assert_eq!(iter.len(), 3);
            first
        };
        assert_eq!(first, Some("alpha"));
        assert_eq!(
            variant.array_iter_str()?.collect::<Vec<_>>(),
            ["alpha", "βήτα", "", "終"]
        );
        Ok(())
    }
    #[test]
    fn reverse_and_double_ended_mix() -> TestResult {
        let variant = ["0", "1", "2", "3"].to_variant();
        let mut iter = variant.array_iter_str()?;
        assert_eq!(iter.next_back(), Some("3"));
        assert_eq!(iter.next(), Some("0"));
        assert_eq!(iter.next_back(), Some("2"));
        assert_eq!(iter.next(), Some("1"));
        assert_eq!(iter.next(), None);
        assert_eq!(iter.next_back(), None);
        assert_eq!(iter.len(), 0);
        assert_eq!(
            variant.array_iter_str()?.rev().collect::<Vec<_>>(),
            ["3", "2", "1", "0"]
        );
        Ok(())
    }
    #[test]
    fn nth_and_nth_back() -> TestResult {
        let variant = ["0", "1", "2", "3", "4", "5"].to_variant();
        let mut iter = variant.array_iter_str()?;
        assert_eq!(iter.nth(1), Some("1"));
        assert_eq!(iter.len(), 4);
        assert_eq!(iter.next(), Some("2"));
        assert_eq!(iter.nth_back(2), Some("3"));
        assert_eq!(iter.len(), 0);
        assert_eq!(iter.next(), None);
        assert_eq!(iter.next_back(), None);
        Ok(())
    }
    #[test]
    fn nth_limits_and_overflow_are_fused() -> TestResult {
        let variant = ["0", "1", "2"].to_variant();
        for n in [3, usize::MAX] {
            let mut iter = variant.array_iter_str()?;
            assert_eq!(iter.nth(n), None);
            assert_eq!(iter.next(), None);
            assert_eq!(iter.next_back(), None);
            let mut iter = variant.array_iter_str()?;
            assert_eq!(iter.nth_back(n), None);
            assert_eq!(iter.next(), None);
            assert_eq!(iter.next_back(), None);
        }
        let mut iter = variant.array_iter_str()?;
        assert_eq!(iter.next(), Some("0"));
        assert_eq!(iter.nth(usize::MAX), None);
        assert_eq!(iter.next_back(), None);
        Ok(())
    }
    #[test]
    fn last_after_partial_iteration_and_iterator_drop() -> TestResult {
        let variant = ["0", "1", "2", "3"].to_variant();
        let last = variant.array_iter_str()?.last();
        assert_eq!(last, Some("3"));
        let mut iter = variant.array_iter_str()?;
        assert_eq!(iter.next_back(), Some("3"));
        assert_eq!(iter.next(), Some("0"));
        assert_eq!(iter.last(), Some("2"));
        Ok(())
    }
    #[test]
    #[allow(
        clippy::iter_nth_zero,
        reason = "Exercises nth itself on an empty iterator"
    )]
    fn empty_iterator_and_type_mismatch() -> TestResult {
        let variant = Vec::<String>::new().to_variant();
        let mut iter = variant.array_iter_str()?;
        assert_eq!(iter.len(), 0);
        assert_eq!(iter.next(), None);
        assert_eq!(iter.next_back(), None);
        assert_eq!(iter.nth(0), None);
        assert_eq!(iter.nth_back(0), None);
        assert_eq!(iter.last(), None);
        assert!(42_u32.to_variant().array_iter_str().is_err());
        Ok(())
    }
}
